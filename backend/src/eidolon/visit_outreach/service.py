"""Visit tasks outlive browser subscriptions. Single event-loop owner, single worker."""
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from eidolon.reply_format import Segment, SegmentDelta
from eidolon.store import Conversation, ReachOutRefused
from .policy import ScheduleDecision, DEFAULT_WAIT_PROFILE, dwell, night_key, first_body_deadline

ACTIVE = {'preparing', 'ready', 'typing', 'streaming'}

Generate = Callable[
    [Conversation, Callable[[SegmentDelta], Awaitable[None]], Callable[[], Awaitable[None]]],
    Awaitable[list[Segment]],
]

class OpportunityEnded(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)

class VisitService:
    hold_seconds = 120
    real_generate: Generate | None = None
    message_clock: Callable[[], datetime] = staticmethod(lambda: datetime.now(UTC))
    schedule = None
    wait_profile = DEFAULT_WAIT_PROFILE

    def __init__(self, repository, host, clock, pending=None, visible=None):
        self.repository, self.host, self.clock = repository, host, clock
        self.pending = pending if pending is not None else set()
        self.visible = visible if visible is not None else set()
        self.jobs = {}
        self._creation_lock = asyncio.Lock()
        self._reservations = set()
        self._deleted_users = set()
        self._loop = None
        self._closing = False

    async def opportunity(self, c, night, tier):
        return (await self.repository.opportunity(c, night, tier))

    async def opportunity_history(self, user):
        return (await self.repository.opportunity_history(user))

    async def status(self, user):
        return (await self.repository.status(user))

    async def recent_openings(self, c):
        return (await self.repository.recent_openings(c))

    def dwell(self, c, night, override=-1):
        return dwell(c, night, self.schedule_decision(c), override, now=self.clock(), profile=self.wait_profile)

    async def write(self, job):
        if (await self.repository.write(job)):
            return True
        job.update(state='cancelled',reason='owner_deleted',ja='',zh='',parts=[],message=None)
        self.jobs.pop(job['key'], None)
        return False

    async def find(self, key):
        if key not in self.jobs:
            job = (await self.repository.find(key))
            if job is not None and key[0] not in self._deleted_users:
                self.jobs.setdefault(key, job)
        return self.jobs.get(key)

    async def valid(self, c, contact):
        return (await self.host.valid(c, contact))

    def forget_user(self, user):
        self._deleted_users.add(user)
        # Legacy deletion endpoints can run in a worker thread; mutate live jobs on
        # their owner loop. Mark deletion immediately to reject in-flight reservations.
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._loop is not None and current_loop is not self._loop:
            self._loop.call_soon_threadsafe(self._forget_jobs, user)
        else:
            self._forget_jobs(user)

    def _forget_jobs(self, user):
        for key, job in list(self.jobs.items()):
            if key[0] != user:
                continue
            task = job.get('task')
            if task is not None and not task.done():
                task.get_loop().call_soon_threadsafe(task.cancel)
            job.update(state='cancelled', reason='account_deleted', ja='', zh='', parts=[], message=None)
            self.pending.discard(job['conversation'])
            self.visible.discard(job['conversation'])
            self.jobs.pop(key, None)


    def schedule_decision(self, c):
        return self.schedule(c, self.clock()) if self.schedule is not None else ScheduleDecision()


    async def resumable(self, c, night):
        # Active work belongs to its original opportunity, even after the window closes.
        active = next((job for job in self.jobs.values()
                       if job['key'][0] == c.user_id and job['conversation'] == c.id
                       and job['state'] in ACTIVE), None)
        return active or (await self.find((c.user_id, c.character_id, night)))


    async def create(self, c, night, contact, config, deadline):
        if self._closing:
            return None
        self._loop = asyncio.get_running_loop()
        # An HTTP disconnect must not cancel a queued reservation before its job starts.
        reservation = asyncio.create_task(self._create(c, night, contact, config, deadline))
        self._reservations.add(reservation)
        reservation.add_done_callback(self._reservation_done)
        return await asyncio.shield(reservation)

    def _reservation_done(self, task):
        self._reservations.discard(task)
        if not task.cancelled():
            task.exception()

    async def _create(self, c, night, contact, config, deadline):
        async with self._creation_lock:
            if c.user_id in self._deleted_users:
                return None
            cutoff = time.time() - 7 * 86400
            for old_key, old in list(self.jobs.items()):
                if old['state'] not in ACTIVE and old['created'] < cutoff:
                    self.jobs.pop(old_key)
            await self.repository.prune(cutoff)
            key = (c.user_id, c.character_id, night)
            previous = await self.find(key)
            if previous:
                return previous
            job = dict(key=key, conversation=c.id, contact=contact.isoformat(), state='preparing',
                       calls=1, successes=0, ja='', zh='', message=None, subscribers=0,
                       created=time.time(), deadline=deadline.isoformat())
            result, previous = await self.repository.reserve(job)
            if c.user_id in self._deleted_users:
                return None
            if result == 'existing':
                return self.jobs.setdefault(key, previous)
            if result != 'created':
                return None
            job = self.jobs.setdefault(key, job)
            self.pending.add(c.id)
            job['task'] = asyncio.create_task(self.run(job, c, contact, config, deadline))
            return job

    async def aclose(self):
        self._closing = True
        if self._reservations:
            await asyncio.gather(*self._reservations, return_exceptions=True)
        tasks = [job['task'] for job in self.jobs.values()
                 if job.get('task') is not None and not job['task'].done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def save_result(self, job, c, said, contact):
        # Database.write commits even when its waiter is cancelled. Finish observing
        # the commit before recording terminal state, including during shutdown/timeout.
        task = asyncio.create_task(self.host.save(c, said, contact, job['typing_at']))
        try:
            message = await asyncio.shield(task)
        except asyncio.CancelledError:
            message = await task
        job.update(state='done', message=message.shown(), successes=1)


    async def require_valid(self, c, contact, deadline=None):
        if not (await self.valid(c, contact)):
            spoke = (await self.host.user_spoke(c, contact))
            raise OpportunityEnded('user_spoke' if spoke else 'conversation_changed')
        if deadline is not None and self.clock() >= deadline:
            raise OpportunityEnded('night_ended')


    async def run(self, job, c, contact, config, deadline):
        # Retrieval precedes the timed request. Start one monotonic deadline at the chat LLM call.
        try:
            async with asyncio.timeout(None) as timer:
                async def requested():
                    if 'llm_requested_at' in job:
                        return
                    (await self.require_valid(c, contact, deadline))
                    job['llm_requested_at'] = datetime.now(UTC).isoformat()
                    job['expires_at'] = time.time() + self.hold_seconds
                    timer.reschedule(asyncio.get_running_loop().time() + self.hold_seconds)
                    (await self.write(job))
                await self._run(job, c, contact, config, deadline, requested)
        except TimeoutError:
            if job['state'] == 'done':
                return  # Formal message committed; a slow snapshot is not a failed generation.
            job.update(state='expired', reason='llm_timeout')
            (await self.write(job))


    async def start_typing(self, job, c):
        job.setdefault('typing_at', self.message_clock().isoformat())
        job['state'] = 'typing'
        self.visible.add(c.id)
        (await self.write(job))


    async def _run(self, job, c, contact, config, deadline, requested):
        try:
            await self.run_real(job, c, contact, config, deadline, requested)
        except OpportunityEnded as ended:
            job.update(state='yielded' if ended.reason == 'user_spoke' else 'expired', reason=ended.reason)
        except ReachOutRefused:
            spoke = (await self.host.user_spoke(c, contact))
            job.update(state='yielded' if spoke else 'expired', reason='user_spoke' if spoke else 'store_refused')
        except asyncio.CancelledError:
            job['state'] = 'interrupted'
            raise
        except Exception as error:
            import logging
            logging.getLogger(__name__).warning('Night visit failed: %s', type(error).__name__)
            job['state'] = 'failed'
        finally:
            try:
                await self.write(job)
            finally:
                self.pending.discard(c.id)
                self.visible.discard(c.id)


    async def run_real(self, job, c, contact, config, deadline, requested):
        generate = self.real_generate
        if generate is None:
            raise RuntimeError('No visit generator configured')
        (await self.require_valid(c, contact, deadline))
        async def delta(event):
            if job['state'] == 'preparing':
                job['state'] = 'ready'
                (await self.write(job))
                target = job['created'] + config.first_body_delay
                cue = target - min(config.typing_lead, config.first_body_delay)
                # A requested reply belongs to the backend task, even before typing.
                # Keep presentation timing, but never wait for the browser to return.
                while time.time() < cue:
                    (await self.require_valid(c, contact, deadline))
                    await asyncio.sleep(.05)
                (await self.require_valid(c, contact, deadline))
                (await self.start_typing(job, c))
                # Count from preparation start, never from reconnect or model readiness.
                await asyncio.sleep(max(0, target-time.time()))
                (await self.require_valid(c, contact, deadline))
            job['state'] = 'streaming'
            parts = job.setdefault('parts', [])
            while len(parts) <= event.segment:
                parts.append({'ja':'','zh':''})
            parts[event.segment][event.lang] += event.text
            job[event.lang] += event.text
            (await self.write(job))
        said = await generate(c, delta, requested)
        if not said:
            job['state'] = 'silent'
            return
        await self.save_result(job, c, said, contact)


    async def events(self, job):
        def frame(event):
            return 'data: ' + json.dumps(event,ensure_ascii=False) + '\n\n'
        job['subscribers'] += 1
        shown = False
        lengths = {'ja':0,'zh':0}
        part_lengths = {}
        try:
            if job['state'] == 'done':
                yield frame({'type':'recorded'})
                return
            while True:
                state = job['state']
                if state in ('typing','streaming','done'):
                    if not shown:
                        yield frame({'type':'typing', 'at':job.get('typing_at')})
                        shown = True
                    for index, part in enumerate(job.get('parts', [])):
                        for lang in ('ja','zh'):
                            key = (index,lang)
                            text = part[lang][part_lengths.get(key,0):]
                            if text:
                                part_lengths[key] = len(part[lang])
                                yield frame({'type':'delta','segment':index,'lang':lang,'text':text})
                    for lang in ([] if 'parts' in job else lengths):
                        text = job[lang][lengths[lang]:]
                        if text:
                            lengths[lang] = len(job[lang])
                            yield frame({'type':'delta','segment':0,'lang':lang,'text':text})
                if state == 'done':
                    yield frame({'type':'reach-out-done','message':job['message']})
                    return
                if state not in ACTIVE:
                    return
                await asyncio.sleep(.03)
        finally:
            job['subscribers'] -= 1


    def note_opening(self, c, kind):
        if kind not in ('topic', 'encounter'):
            return
        for job in self.jobs.values():
            if job['conversation'] == c.id and job['state'] in ACTIVE:
                job['opening'] = kind
                return



    async def period(self, c, night):
        return night

    async def inspect_visit(self, c, now, override=-1):
        night = await self.period(c, night_key(now))
        previous = (await self.resumable(c, night))
        if previous and previous['conversation'] == c.id:
            return {'seconds':0, 'restoring':True}
        reason, current, relationship = (await self.host.eligibility(c, now))
        if reason:
            return {'seconds':None, 'reason':reason}
        if not self.schedule_decision(c).allowed:
            return {'seconds':None, 'deferred':True, 'reason':'schedule'}
        chance = (await self.opportunity(c, night, relationship.tier if relationship else None))
        if not chance['eligible']:
            return {'seconds':None, 'reason':'opportunity_missed', 'opportunity':chance}
        return self.dwell(c, night, override) | {'opportunity':chance}

    async def start_or_resume(self, c, now, presentation):
        night = await self.period(c, night_key(now))
        previous = (await self.resumable(c, night))
        if previous and previous['conversation'] == c.id:
            return previous, None
        if not self.schedule_decision(c).allowed:
            return None, 'schedule'
        reason, current, relationship = (await self.host.eligibility(c, now))
        if previous or reason:
            return None, reason
        if not (await self.opportunity(c, night, relationship.tier if relationship else None))['eligible']:
            return None, 'opportunity_missed'
        return (await self.create(c, night, current.last_contact, presentation, first_body_deadline(now))), None

    def subscribe(self, job):
        return self.events(job)
