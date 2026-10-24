"""Native async host wiring. The browser owns visibility; the service owns work."""
from contextvars import ContextVar
from datetime import datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal
import hashlib
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel
from eidolon.sessions import UserId
from eidolon.producer import effective_timezone
from eidolon.store import Conversation
from .host import EidolonHost, Eligibility
from .repository import Repository
from .service import VisitService
from .policy import night_key

if TYPE_CHECKING:
    from eidolon.assembly import Backend

_zone = ContextVar('visit_zone', default=ZoneInfo('Asia/Shanghai'))


def _prepare(db):
    db.execute('CREATE TABLE IF NOT EXISTS visit_outreach_on (user_id TEXT PRIMARY KEY REFERENCES users(id))')
    if 'tonight' not in {r[1] for r in db.execute('PRAGMA table_info(visit_outreach_on)')}:
        db.execute('ALTER TABLE visit_outreach_on ADD COLUMN tonight TEXT')
    if 'invitation' not in {r[1] for r in db.execute('PRAGMA table_info(visit_outreach_on)')}:
        db.execute('ALTER TABLE visit_outreach_on ADD COLUMN invitation TEXT')



def _enabled(db, user_id):
    return db.execute('SELECT 1 FROM visit_outreach_on WHERE user_id=?', (user_id,)).fetchone() is not None


def _switch(db, user_id, on):
    if on:
        db.execute('INSERT OR IGNORE INTO visit_outreach_on(user_id) SELECT id FROM users WHERE id=?', (user_id,))
    else:
        db.execute('DELETE FROM visit_outreach_on WHERE user_id=?', (user_id,))


def _mode(db, user_id, night):
    row = db.execute('SELECT tonight FROM visit_outreach_on WHERE user_id=?', (user_id,)).fetchone()
    return 'off' if row is None else 'tonight' if row[0] == night else 'natural'


def _set_mode(db, user_id, mode, night):
    _switch(db, user_id, mode != 'off')
    db.execute('UPDATE visit_outreach_on SET tonight=?, invitation=NULL WHERE user_id=?',
               (night if mode == 'tonight' else None, user_id))


def _visit_period(db, c, night, contact):
    if _mode(db, c.user_id, night) != 'tonight':
        return night
    # Re-entry, refresh and mode toggles reuse this contact's attempt, including failures.
    # Preserve pre-migration invitation tasks for the same contact as well.
    import json
    rows = db.execute('SELECT night,snapshot FROM dev2_night_tasks WHERE user=? AND character=?',
                      (c.user_id,c.character_id)).fetchall()
    for period, snapshot in rows:
        data = json.loads(snapshot)
        if period.startswith(night+'#invite:') and data.get('contact') == contact and data.get('conversation') == c.id:
            return period
    token = hashlib.sha256(f'{c.id}:{contact}'.encode()).hexdigest()[:32]
    return f'{night}#invite:{token}'



class Host(EidolonHost):
    def __init__(self, backend: 'Backend'):
        super().__init__(backend.store, backend.calls, backend.clock, database=backend.database,
                         relationship_lookup=lambda c: backend.affinities.status(c.user_id, c.character_id))
        self.backend = backend

    async def mode(self, user_id):
        profile = await self.store.get_profile(user_id)
        now = self.utc_clock().astimezone(ZoneInfo(effective_timezone(profile, None)))
        return await self.database.read(_mode, user_id, night_key(now))

    async def prefer_opening(self, c):
        return await self.mode(c.user_id) == 'tonight'

    async def enabled(self, user_id):
        return await self.database.read(_enabled, user_id)

    async def eligibility(self, c: Conversation, now: datetime) -> Eligibility:
        if not await self.enabled(c.user_id):
            return 'disabled', None, None
        limits = await self.backend.usage.user_limits(c.user_id)
        if limits is not None and limits.turns_per_hour == 0:
            return 'restricted', None, None
        reason, current, relationship = await super().eligibility(c, now)
        if reason == 'recent_contact' and await self.prefer_opening(c):
            reason = 'busy' if self.store.is_taking_turn(c.id) or self.calls.in_call(c.id) else None
        return reason, current, relationship

    async def valid(self, c, contact):
        return await self.enabled(c.user_id) and await super().valid(c, contact)


class Service(VisitService):
    async def period(self, c, night):
        contact = await self.host.last_contact(c.user_id, c.character_id)
        return await self.host.database.read(_visit_period, c, night, contact.isoformat() if contact else None)

    async def inspect_visit(self, c, now, override=-1):
        # The explicit tonight experience uses the fast range throughout the night.
        # Eligibility, stored draws, schedule deferral and task restoration stay shared.
        if await self.host.prefer_opening(c):
            override = 0
        return await super().inspect_visit(c, now, override)

    async def opportunity(self, c, night, tier):
        chance = await super().opportunity(c, night.split('#invite:', 1)[0], tier)
        if await self.host.prefer_opening(c):
            return chance | {'eligible': True, 'mode': 'tonight'}
        return chance

    async def run(self, job, c, contact, config, deadline):
        recording = self.host.backend.activity.begin('reach_out', c.user_id, c.character_id,
                                                     own_llm=False, exempt=False)
        try:
            with recording.current():
                await super().run(job, c, contact, config, deadline)
        finally:
            state = job['state']
            result = ('success' if state == 'done' else 'failed' if state == 'failed' or
                      job.get('reason') == 'llm_timeout' else 'left' if state == 'interrupted' else 'rejected')
            await recording.finish(result)
            if state == 'done' and job.get('message'):
                await self.host.store.reach_out_recorded_as(job['message']['id'], recording.row_id)


class ModeInput(BaseModel):
    mode: Literal['off', 'natural', 'tonight']


def build_visits(backend: 'Backend', *, context_factory=None) -> Service:
    backend.database.prepare(_prepare)
    host = Host(backend)
    visits = Service(Repository(backend.database), host, lambda: backend.clock().astimezone(_zone.get()))
    visits.message_clock = backend.clock
    from .context import V020Context
    from .generator import VisitGenerator
    context = (context_factory or V020Context)(backend)
    visits.real_generate = VisitGenerator(context, host, visits)
    backend.visits = visits
    backend._closers.append(visits.aclose)
    return visits


def install(app, backend):
    visits = backend.visits
    presentation = SimpleNamespace(first_body_delay=4, typing_lead=2)

    async def conversation(conversation_id, user_id):
        c = await backend.store.get_conversation(conversation_id)
        if c is None or c.user_id != user_id:
            raise HTTPException(404)
        profile = await backend.store.get_profile(user_id)
        _zone.set(ZoneInfo(effective_timezone(profile, None)))
        return c

    @app.put('/api/account/visit-outreach-mode', status_code=204)
    async def change_mode(body: ModeInput, user_id: UserId):
        profile = await backend.store.get_profile(user_id)
        now = backend.clock().astimezone(ZoneInfo(effective_timezone(profile, None)))
        await backend.database.write(_set_mode, user_id, body.mode, night_key(now))

    @app.get('/api/visit-outreach/dwell/{conversation_id}')
    async def dwell(conversation_id: str, user_id: UserId):
        c = await conversation(conversation_id, user_id)
        # Existing visible work can still be restored after the switch is turned off.
        return await visits.inspect_visit(c, visits.clock())

    @app.post('/api/visit-outreach/{conversation_id}')
    async def enter(conversation_id: str, user_id: UserId):
        c = await conversation(conversation_id, user_id)
        job, reason = await visits.start_or_resume(c, visits.clock(), getattr(visits, 'presentation', presentation))
        if job is None:
            return JSONResponse({'skipped': True, 'reason': reason})
        return StreamingResponse(visits.subscribe(job), media_type='text/event-stream',
                                 headers={'Cache-Control':'no-cache', 'X-Accel-Buffering':'no'})
