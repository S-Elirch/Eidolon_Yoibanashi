"""Visit history formatting, adapted from eidolon-sc v0.2.0 engine.py (AGPL-3.0).

Owned here to avoid importing the Engine or its private methods. Keep parity tests
for gifts, prior reach-outs, history trimming and photo context when upgrading.
"""
from collections.abc import Mapping, Sequence
from datetime import datetime
from zoneinfo import ZoneInfo
from eidolon.gift_catalog import Gift
from eidolon.reply_format import format_reply
from eidolon.store import Message, ConversationEvent, ReachOut, Recorded

def latest_turns(messages: list[Message], turns: int | None) -> list[Message]:
    """The messages from the `turns`th last user message on (from the first if None), so
    what the chat model reads always opens with the user."""
    starts = [i for i, m in enumerate(messages) if m.role == "user"]
    kept = starts if turns is None else starts[-turns:]
    return messages[kept[0]:] if kept else []


def chat_history(messages: list[Message], turns: int | None) -> list[Message]:
    """The latest turns of `messages` the chat model reads: at least `turns` (all if None),
    cut back to that many each time they reach half as many again, rather than one at a
    time, so what it reads opens the same way from turn to turn in between."""
    total = sum(m.role == "user" for m in messages)
    if turns is None or total <= turns:
        return latest_turns(messages, None)
    step = max(1, turns // 2)
    return latest_turns(messages, total - step * ((total - turns) // step))


RESPOND_TO_GIFTS = (
    "（先回应制作人说的话，再在合适的时候用你自己的方式回应收到的礼物，一次就够。）"
)
"""Told her after the gifts she has not replied since, once however many there are."""


def _narration(event: ConversationEvent, gift_catalog: Mapping[str, Gift]) -> str:
    """What she is told of `event`, as it was."""
    if event.kind == "hung_up":
        return "（你挂断了制作人的电话。）"
    if event.kind == "ended":  # only read by a turn it will reject
        return "（你结束了和制作人的对话。）"
    assert event.gift_id is not None
    liked = "这是你喜欢的东西。" if event.liked else ""
    return f"（制作人送给你一份{gift_catalog[event.gift_id].name}。{liked}）"


def _reached_out(reach_out: ReachOut, timezone: str) -> str:
    """What she is told of her `reach_out`, the producer in `timezone`: when, then her words
    as her replies are written in her prompt."""
    at = datetime.fromisoformat(reach_out.created_at).astimezone(ZoneInfo(timezone))
    said = format_reply(reach_out.segments)
    return f"（{at.month}月{at.day}日 {at:%H:%M}，你先给制作人发了：）{said}"


def _narration_line(
    entry: ConversationEvent | ReachOut, gift_catalog: Mapping[str, Gift], timezone: str
) -> str:
    """The line of narration telling her of `entry`, the producer in `timezone`."""
    if isinstance(entry, ReachOut):
        return f"{_reached_out(entry, timezone)}\n"
    return f"{_narration(entry, gift_catalog)}\n"


def narrations(
    record: Sequence[Recorded], gift_catalog: Mapping[str, Gift], timezone: str
) -> dict[int | None, str]:
    """The narration of the events and reach-outs in `record`, by the id of the user message
    each comes before, None for those after them all: one line for each, then, if she has
    neither replied nor reached out since a gift, `RESPOND_TO_GIFTS`. The producer is in
    `timezone`."""
    lines: list[str] = []
    told: dict[int | None, str] = {}
    unanswered = False
    for entry in record:
        if not isinstance(entry, Message):
            lines.append(_narration_line(entry, gift_catalog, timezone))
            if isinstance(entry, ReachOut):
                unanswered = False  # reaching out, she has responded to the gifts before
            else:
                unanswered = unanswered or entry.kind == "gift"
        elif entry.role == "character":
            unanswered = False
        elif lines:
            told[entry.id], lines = "".join(lines), []
    if unanswered:
        lines.append(f"{RESPOND_TO_GIFTS}\n")
    if lines:
        told[None] = "".join(lines)
    return told


def since_last_turn(
    record: Sequence[Recorded], gift_catalog: Mapping[str, Gift], timezone: str
) -> str:
    """The narration of the events and reach-outs after the last turn, one line each, the
    producer in `timezone`."""
    lines: list[str] = []
    for entry in record:
        if isinstance(entry, Message):
            lines = []
        else:
            lines.append(_narration_line(entry, gift_catalog, timezone))
    return "".join(lines)


