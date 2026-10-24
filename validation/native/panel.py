"""Local dev4 test entry point. Never imported by the production application."""
import asyncio
import json
import os
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from eidolon.sessions import UserId, set_session_cookie
from eidolon.visit_outreach.policy import ScheduleDecision, night_key
from eidolon.visit_outreach.runtime import _set_mode

ZONE = ZoneInfo('Australia/Sydney')


class Controls(BaseModel):
    local: datetime
    simulated: bool = True
    paused: bool = False
    dwell_tier: int = Field(default=-1, ge=-1, le=2)
    first_body_delay: float = Field(default=4, ge=0, le=30)
    typing_lead: float = Field(default=2, ge=0, le=5)


class TestClock:
    def __init__(self, path):
        self.path = path
        self.controls = Controls(local=datetime.now(ZONE).replace(tzinfo=None), simulated=False)
        self.anchor = time.time()
        self.last_stamp = None
        if path.exists():
            saved = json.loads(path.read_text())
            self.controls = Controls.model_validate(saved['controls'])
            self.anchor = saved['anchor']

    def __call__(self):
        if not self.controls.simulated:
            return datetime.now(UTC)
        elapsed = 0 if self.controls.paused else time.time() - self.anchor
        return (self.controls.local.replace(tzinfo=ZONE) + timedelta(seconds=elapsed)).astimezone(UTC)

    def stamp(self):
        now = self()
        if self.last_stamp is not None and now <= self.last_stamp:
            now = self.last_stamp + timedelta(microseconds=1)
        self.last_stamp = now
        return now.isoformat()

    def save(self, controls):
        self.controls, self.anchor, self.last_stamp = controls, time.time(), None
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'controls': controls.model_dump(mode='json'), 'anchor': self.anchor}))
        temporary.replace(self.path)


class Jump(BaseModel):
    action: str


def install(app, backend, clock):
    visits = backend.visits
    visits.presentation = SimpleNamespace(first_body_delay=clock.controls.first_body_delay,
                                         typing_lead=clock.controls.typing_lead)
    def schedule(c, now):
        tier = clock.controls.dwell_tier
        return ScheduleDecision(probabilities=tuple(100 if n == tier else 0 for n in range(3)) if tier >= 0 else None)
    visits.schedule = schedule
    lock = asyncio.Lock()

    async def idle():
        if visits.pending or backend.calls.in_progress or any(
            backend.store.is_taking_turn(x.conversation.id) for x in await backend.store.current_conversations()
        ):
            raise HTTPException(409, '请等待搭话、普通回复或通话结束后再调整时间或创建新轮次。')

    def state():
        now = clock().astimezone(ZONE)
        return clock.controls.model_dump(mode='json') | {
            'local': now.replace(tzinfo=None).isoformat(timespec='seconds'),
            'night': night_key(now), 'probabilities': visits.wait_profile.probabilities_at(now),
        }

    @app.get('/api/dev2/panel', response_class=HTMLResponse)
    @app.get('/api/dev4/panel', response_class=HTMLResponse)
    async def panel():
        return Path(__file__).with_name('panel.html').read_text(encoding='utf-8')

    @app.get('/api/dev4/state')
    async def status(user_id: UserId):
        user = await backend.store.get_user(user_id)
        users = await backend.store.list_users()
        return state() | {'user': user.name, 'user_id': user_id,
            'accounts': [{'id': u.id, 'name': u.name} for u in users if u.name.startswith(('dev2', 'dev4'))],
            'tasks': await visits.status(user_id), 'opportunities': await visits.opportunity_history(user_id),
            'mode': await visits.host.mode(user_id)}

    @app.post('/api/dev4/controls')
    async def controls(body: Controls, user_id: UserId):
        async with lock:
            await idle()
            if body.local.tzinfo is not None:
                raise HTTPException(422, '请输入不带时区的 Sydney 本地时间。')
            clock.save(body)
            visits.presentation = SimpleNamespace(first_body_delay=body.first_body_delay, typing_lead=body.typing_lead)
            response = JSONResponse(state())
            # A multi-day jump must not strand this browser with an expired test session.
            set_session_cookie(response, await backend.store.create_session(user_id, clock()), backend.settings)
            return response

    @app.post('/api/dev4/jump')
    async def jump(body: Jump, user_id: UserId):
        now = clock().astimezone(ZONE).replace(tzinfo=None)
        if body.action == '30m': target = now + timedelta(minutes=30)
        elif body.action == '1d': target = now + timedelta(days=1)
        elif body.action == '22':
            target = now.replace(hour=22, minute=0, second=0, microsecond=0)
            if target <= now: target += timedelta(days=1)
        else: raise HTTPException(422, '未知跳时操作')
        return await controls(clock.controls.model_copy(update={'local': target, 'simulated': True}), user_id)

    @app.post('/api/dev4/select/{selected}')
    async def select(selected: str, user_id: UserId):
        await idle()
        user = await backend.store.get_user(selected)
        if not user.name.startswith(('dev2', 'dev4')):
            raise HTTPException(403, '仅可切换开发测试账号')
        response = JSONResponse({'selected': user.name})
        set_session_cookie(response, await backend.store.create_session(selected, clock()), backend.settings)
        return response

    @app.post('/api/dev4/reset')
    async def reset(user_id: UserId):
        async with lock:
            await idle()
            # Native data-package round trip, with old records and draws left intact.
            with tempfile.TemporaryDirectory() as temporary:
                package = Path(temporary) / 'round.zip'
                await backend.data_packages.export(user_id, ['profile', 'conversations', 'memories'], package)
                fresh = await backend.store.create_user('dev4-round-' + str(time.time_ns()))
                try:
                    await backend.data_packages.import_(fresh.id, package, ['profile', 'conversations', 'memories'])
                    def copy_relationships(db):
                        db.execute('INSERT INTO affinities(user_id,character_id,value,updated_at,cooling) '
                                   'SELECT ?,character_id,value,updated_at,cooling FROM affinities WHERE user_id=?', (fresh.id,user_id))
                    await backend.database.write(copy_relationships)
                    await backend.store.consent(fresh.id, clock())
                    await backend.database.write(_set_mode, fresh.id, 'natural', night_key(clock().astimezone(ZONE)))
                except Exception:
                    await backend.accounts.delete(fresh.id)
                    raise
            response = JSONResponse({'message':'已复制资料、对话、记忆与关系到新轮次；旧记录保留。未回应搭话与近期交流规则仍有效。'})
            set_session_cookie(response, await backend.store.create_session(fresh.id, clock()), backend.settings)
            return response


def create():
    if os.environ.get('EIDOLON_DEV4_PANEL') != '1':
        raise RuntimeError('Dev4 panel must be explicitly enabled')
    from eidolon.assembly import build
    from eidolon.app import create_app
    from eidolon.config import Settings
    import eidolon.store as store_module
    settings = Settings.from_env()
    if not settings.dev_login or settings.database_path.parent != Path('/data'):
        raise RuntimeError('Panel requires local dev login and the dev data mount')
    clock = TestClock(settings.database_path.with_name('dev4-panel-clock.json'))
    # Test-process-only timestamp provider; Engine source and its clock are untouched.
    store_module._now = clock.stamp
    backend = build(replace(settings, reach_outs=False), clock=clock)
    async def start():
        await backend.config.start()
        if backend.corpus is not None:
            backend._loading_stories = asyncio.create_task(backend.corpus.load())
    backend.start = start  # No scheduled outreach or provider probes while jumping days.
    app = create_app(backend)
    install(app, backend, clock)
    return app
