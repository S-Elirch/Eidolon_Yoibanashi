"""Adapter for the upstream async Store and Engine; inject existing host services."""
from collections.abc import Awaitable, Callable
from datetime import datetime

from eidolon.affinity import Status
from eidolon.call import Calls
from eidolon.clock import Clock
from eidolon.data_access import Database
from eidolon.store import Conversation, CurrentConversation, Store
from .policy import eligibility

RelationshipLookup = Callable[[Conversation], Awaitable[Status | None]]
# Host adapters may add reasons, for example a schedule or deployment restriction.
Eligibility = tuple[str | None, CurrentConversation | None, Status | None]


class EidolonHost:
    def __init__(self, store: Store, calls: Calls, utc_clock: Clock, *, database: Database,
                 relationship_lookup: RelationshipLookup | None = None):
        self.store, self.calls, self.utc_clock = store, calls, utc_clock
        self.relationship_lookup = relationship_lookup
        self.database = database

    async def eligibility(self, c: Conversation, now: datetime) -> Eligibility:
        relationship = await self.relationship(c)
        current = next((x for x in (await self.store.current_conversations()) if x.conversation.id == c.id), None)
        reason = eligibility(now, self.utc_clock(), relationship, current,
                             self.store.is_taking_turn(c.id) or self.calls.in_call(c.id))
        return reason, current, relationship

    async def relationship(self, c: Conversation) -> Status | None:
        lookup = self.relationship_lookup
        return await lookup(c) if lookup is not None else None

    async def last_contact(self, user_id, character_id):
        from eidolon.store import read_last_contact
        return await self.database.read(read_last_contact, user_id, character_id)

    async def valid(self, c, contact):
        latest = await self.store.list_conversations(c.user_id, c.character_id)
        return (latest and latest[0][0].id == c.id and not latest[0][2]
                and (await self.last_contact(c.user_id, c.character_id)) == contact
                and not self.store.is_taking_turn(c.id) and not self.calls.in_call(c.id))

    async def user_spoke(self, c, contact):
        return ((await self.last_contact(c.user_id,c.character_id)) != contact
                or self.store.is_taking_turn(c.id))

    async def save(self, c, said, contact, at):
        return await self.store.add_reach_out(c.id, said, contact, self.calls.in_call(c.id), created_at=at)

    async def prefer_opening(self, c) -> bool:
        return False

