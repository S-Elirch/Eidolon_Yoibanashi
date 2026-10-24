"""Host context adapters. No imports from or calls into ConversationEngine."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo
from eidolon.affinity import affinity_prompt
from eidolon.memory import last_turn_query
from eidolon.producer import effective_timezone, local_date, now_prompt, producer_prompt
from eidolon.store import Message, read_record, read_last_contact, read_profile
from .history import chat_history, narrations, since_last_turn

@dataclass(frozen=True)
class VisitContext:
    character: object
    producer: str
    history: list
    narration: dict
    shared: list[str]
    about_now: str
    memory: str | None
    tier: str

class LegacyContext:
    """Async host contract without schedule/calendar context; newer abilities are optional."""
    def __init__(self, backend):
        self.backend = backend

    async def before_snapshot(self, c):
        return None

    def extra_context(self, character, now, jobs):
        return [], []

    def snapshot(self, db, c, affinity_on):
        b = self.backend
        return (read_record(db, c.id),
                b.affinities.read_status(db, c.user_id, c.character_id) if affinity_on else None,
                read_last_contact(db, c.user_id, c.character_id), read_profile(db, c.user_id))

    async def prepare(self, c):
        b = self.backend
        jobs = await self.before_snapshot(c)
        record, affinity, contact, profile = await b.database.read(self.snapshot, c, b.affinities.on)
        character = b.characters[c.character_id]
        zone = effective_timezone(profile, None)
        now = b.clock()
        messages = [entry for entry in record if isinstance(entry, Message)]
        told = narrations(record, b.gift_catalog, zone)
        told[None] = since_last_turn(record, b.gift_catalog, zone)
        for message in messages:
            if message.role == 'user':
                sent = datetime.fromisoformat(message.created_at).astimezone(ZoneInfo(zone))
                told[message.id] = f'（消息发送时间 {sent:%Y-%m-%d %H:%M}，不代表事件发生时间。）\n' + told.get(message.id, '')
        memory = await b.memories.for_reaching_out(c, zone, last_turn_query(messages))
        shared, extra = self.extra_context(character, now, jobs)
        if b.world is not None:
            world = b.world.prompt(character.name, local_date(now, zone))
            if world:
                shared.append(world)
        about_now = now_prompt(profile, now, zone, 'apart',
                               affinity_prompt(affinity.tier, None) if affinity else [], contact)
        return VisitContext(character, producer_prompt(profile), chat_history(messages, b.config.history_turns),
                            told, shared, '\n'.join([about_now, *extra]), memory,
                            affinity.tier if affinity else 'trust')

class V020Context(LegacyContext):
    """Schedules can settle affinity, so fetch them before the conversation snapshot."""
    async def before_snapshot(self, c):
        return await self.backend.schedules.hers(c.user_id, c.character_id, None)

    def extra_context(self, character, now, jobs):
        from eidolon.schedule import schedule_lines
        from eidolon.today import her_birthday, today_lines
        return ([her_birthday(character)],
                [*today_lines(now, character, self.backend.characters.values(), 'apart'),
                 *(schedule_lines(jobs) if jobs is not None else [])])
