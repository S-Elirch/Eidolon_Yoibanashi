"""Visit-owned prompt assembly and streaming; the upstream Engine stays untouched."""
from dataclasses import replace
from eidolon.llm import ChatMessage
from eidolon.photos import NOT_RECEIVED
from eidolon.reply_format import ReplyParser, SegmentDelta, format_instructions, format_reply
from .prompt import instructions, opening_type

class EmptyVisitReply(RuntimeError):
    pass


def build_prompt(context, recent, prefer_opening):
    messages = [ChatMessage('system', '\n\n'.join([
        context.character.sheet, *context.shared, format_instructions(None)]), cache_breakpoint=True),
        ChatMessage('system', context.producer)]
    for entry in context.history:
        if entry.role == 'user':
            messages.append(ChatMessage('user', context.narration.get(entry.id, '') + (entry.text or '')))
        else:
            messages.append(ChatMessage('assistant', format_reply(
                entry.segments, entry.photo.tag if entry.photo else None, entry.photo_failed)))
    if context.history:
        messages[-1] = replace(messages[-1], cache_breakpoint=True)
    closing = [context.about_now]
    if any(entry.photo_failed for entry in context.history):
        closing.append(NOT_RECEIVED)
    if context.memory:
        closing.append(context.memory)
    closing.extend(instructions(context.tier, recent, prefer_opening=prefer_opening))
    messages.append(ChatMessage('system', '\n\n'.join(closing)))
    request = ('（此时自然地先说一句；没有合适的旧话题就轻轻打个招呼。）' if prefer_opening else
               '（你注意到制作人此时来到聊天中。先判断是否值得开口；没有合适内容就保持安静。）')
    messages.append(ChatMessage('user', context.narration.get(None, '') + request))
    return messages


class VisitGenerator:
    def __init__(self, context, host, visits):
        self.context, self.host, self.visits = context, host, visits

    async def __call__(self, c, delta, on_request):
        context = await self.context.prepare(c)
        recent = await self.visits.recent_openings(c)
        prefer = await self.host.prefer_opening(c)
        prompt = build_prompt(context, recent, prefer)
        lease = self.host.backend.config.lease()
        try:
            await on_request()
            parser = ReplyParser()
            prefix = ''
            stream = lease.keys.chat_llm.stream(prompt)
            try:
                async for item in stream:
                    if not isinstance(item, str):
                        continue
                    if len(prefix) < 256:
                        prefix += item
                    for event in parser.feed(item):
                        if isinstance(event, SegmentDelta):
                            await delta(event)
            finally:
                if (close := getattr(stream, 'aclose', None)) is not None:
                    await close()
            if prefix.strip() == '<silent/>':
                return []
            if '<ja>' not in prefix:
                raise EmptyVisitReply('访问搭话未返回有效格式')
            for event in parser.finish():
                if isinstance(event, SegmentDelta):
                    await delta(event)
            if not parser.complete:
                raise EmptyVisitReply('对话模型没有返回任何内容')
            if (kind := opening_type(prefix)) is not None:
                self.visits.note_opening(c, kind)
            return parser.complete
        finally:
            lease.release()
