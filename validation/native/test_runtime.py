import asyncio
import tempfile
import unittest
from pathlib import Path
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
import httpx
from eidolon.app import create_app
from eidolon.config import Settings, LLMSettings
from eidolon.producer import ProducerProfile
from eidolon.reply_format import Segment
from eidolon.store import add_turn
from eidolon.sessions import SESSION_COOKIE
from tests.fakes import build_faked, FakeLLM, FakeCatalog

class NativeRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.now=datetime(2026,10,23,15,tzinfo=UTC)  # Shanghai 23:00.
        self.model=FakeLLM(['<opening type="topic"/><ja>こんばんは</ja><zh>晚上好</zh>'])
        self.backend=build_faked(Settings(Path(self.temp.name)/'test.db',Path('/characters/characters.toml'),
            LLMSettings('http://unused.invalid','fake','fake'),dev_login=True,affinity_and_gifts=False),
            llm=self.model,clock=lambda:self.now,catalog_transport=FakeCatalog().transport())
        self.app=create_app(self.backend)
        self.store=self.backend.store
        self.user=await self.store.create_user('runtime-test')
        await self.store.consent(self.user.id,self.now)
        self.c=await self.store.create_conversation(self.user.id,next(iter(self.backend.characters)))
        await self.backend.database.write(add_turn,self.c.id,'项目可能忙到十一点',[Segment(ja='うん',zh='嗯')])
        await self.backend.database.write(lambda db:db.execute('UPDATE messages SET created_at=?',((self.now-timedelta(hours=2)).isoformat(),)))
        token=await self.store.create_session(self.user.id,self.now)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test',
            headers={'origin':'http://localhost:3008'},cookies={SESSION_COOKIE:token})

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.backend.aclose()
        self.temp.cleanup()

    async def test_toggle_stream_and_activity(self):
        account=(await self.client.get('/api/account')).json()
        self.assertFalse(account['visit_outreach'])
        path='/api/visit-outreach/dwell/'+self.c.id
        self.assertEqual((await self.client.get(path)).json()['reason'],'disabled')
        self.assertEqual((await self.client.put('/api/account/visit-outreach-mode',json={'mode':'natural'})).status_code,204)
        self.assertTrue((await self.client.get('/api/account')).json()['visit_outreach'])
        self.assertIsNotNone((await self.client.get(path)).json()['seconds'])
        response=await self.client.post('/api/visit-outreach/'+self.c.id)
        self.assertIn('reach-out-done',response.text)
        tasks=list(self.backend.visits.jobs.values())
        await asyncio.gather(*(j['task'] for j in tasks))
        def records(db):
            return [tuple(r) for r in db.execute("SELECT kind,result FROM activity_records WHERE user_id=?",(self.user.id,))]
        self.assertIn(('reach_out','success'),await self.backend.database.read(records))
        self.assertEqual(len(self.model.prompts),1)
        self.assertIn('recorded',(await self.client.post('/api/visit-outreach/'+self.c.id)).text)

    async def test_ownership_and_user_timezone(self):
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'natural'})
        await self.store.save_profile(self.user.id,ProducerProfile(timezone='UTC'))
        self.assertEqual((await self.client.get('/api/visit-outreach/dwell/'+self.c.id)).json()['reason'],'outside_window')
        other=await self.store.create_user('other')
        c=await self.store.create_conversation(other.id,self.c.character_id)
        self.assertEqual((await self.client.post('/api/visit-outreach/'+c.id)).status_code,404)
        self.assertEqual(self.model.prompts,[])

    async def test_turn_busy_and_deleted_setting(self):
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'natural'})
        self.backend.visits.visible.add(self.c.id)
        response=await self.client.post('/api/conversations/'+self.c.id+'/turns',json={'text':'test'})
        self.assertEqual(response.status_code,409)
        self.backend.visits.visible.clear()
        await self.backend.accounts.delete(self.user.id)
        self.backend.visits.forget_user(self.user.id)
        self.assertFalse(await self.backend.visits.host.enabled(self.user.id))
        self.assertEqual(self.model.prompts,[])

    async def test_tonight_expiry_and_frozen_draw(self):
        from eidolon.visit_outreach.policy import night_key
        self.assertEqual((await self.client.put('/api/account/visit-outreach-mode',json={'mode':'invalid'})).status_code,422)
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        self.assertEqual((await self.client.get('/api/account')).json()['visit_outreach_mode'],'tonight')
        night=night_key(self.now.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')))
        # A missed natural draw is retained, not rerolled by the experience setting.
        await self.backend.visits.repository.opportunity(self.c,night,'cold')
        chance=await self.backend.visits.opportunity(self.c,night,'cold')
        self.assertTrue(chance['eligible'])
        self.assertFalse((await self.backend.visits.repository.opportunity(self.c,night,'cold'))['eligible'])
        self.now+=timedelta(hours=6)  # exactly 05:00 Shanghai
        account=(await self.client.get('/api/account')).json()
        self.assertEqual(account['visit_outreach_mode'],'natural')
        self.assertTrue(account['visit_outreach'])
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'off'})
        self.assertFalse((await self.client.get('/api/account')).json()['visit_outreach'])

    async def test_tonight_fast_wait_preserves_draw_and_schedule(self):
        from eidolon.visit_outreach.policy import ScheduleDecision
        path = '/api/visit-outreach/dwell/' + self.c.id
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'natural'})
        self.backend.visits.schedule = lambda c, now: ScheduleDecision(probabilities=(0,0,100))
        natural = (await self.client.get(path)).json()
        self.assertGreaterEqual(natural['seconds'],15)
        self.assertLessEqual(natural['seconds'],18)
        original = await self.backend.visits.opportunity_history(self.user.id)
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        for hour in (15,19,20):  # Shanghai 23:00, 03:00, 04:00, same night.
            self.now = self.now.replace(hour=hour)
            result = (await self.client.get(path)).json()
            self.assertGreaterEqual(result['seconds'],5)
            self.assertLessEqual(result['seconds'],8)
        self.assertEqual(await self.backend.visits.opportunity_history(self.user.id),original)
        self.backend.visits.schedule = lambda c, now: ScheduleDecision(allowed=False)
        self.assertEqual((await self.client.get(path)).json()['reason'],'schedule')
        self.assertEqual(self.model.prompts,[])

    async def test_tonight_keeps_recent_contact_and_model_rules(self):
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        await self.client.post('/api/visit-outreach/'+self.c.id)
        self.assertEqual(len(self.model.prompts),1)
        self.assertIn('覆盖上文因缺少话题',str(self.model.prompts[0]))
        await self.client.post('/api/visit-outreach/'+self.c.id)
        self.assertEqual(len(self.model.prompts),1)
        # Switching modes does not clear the consumed opportunity/task.
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'natural'})
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        await self.client.post('/api/visit-outreach/'+self.c.id)
        self.assertEqual(len(self.model.prompts),1)

    async def test_tonight_persists_after_completion_and_new_contact(self):
        self.model._replies.append(['<opening type="encounter"/><ja>お疲れさま。</ja><zh>辛苦了。</zh>'])
        from types import SimpleNamespace
        from eidolon.visit_outreach.policy import night_key
        from eidolon.visit_outreach.repository import _write
        from zoneinfo import ZoneInfo
        import time
        visits=self.backend.visits
        visits.presentation=SimpleNamespace(first_body_delay=0,typing_lead=0)
        night=night_key(self.now.astimezone(ZoneInfo('Asia/Shanghai')))
        # The current role and the account have already exhausted natural reservations.
        for i in range(3):
            await self.backend.database.write(_write,dict(key=(self.user.id,self.c.character_id if i==0 else 'other'+str(i),night),
                conversation=self.c.id,state='silent',calls=1,created=time.time()))
        await self.backend.database.write(lambda db:db.execute('UPDATE messages SET created_at=? WHERE conversation_id=?',
            ((self.now-timedelta(minutes=1)).isoformat(),self.c.id)))
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        path='/api/visit-outreach/'+self.c.id
        self.assertIn('reach-out-done',(await self.client.post(path)).text)
        self.assertEqual((await self.client.get('/api/account')).json()['visit_outreach_mode'],'tonight')
        self.assertEqual(await visits.repository.attempts(self.user.id,night),3)
        self.assertEqual(len(self.model.prompts),1)
        # Refresh reuses the completed task; replying permits the next visit without reselecting.
        self.assertIn('recorded',(await self.client.post(path)).text)
        self.assertEqual(len(self.model.prompts),1)
        self.now += timedelta(minutes=1)
        await self.backend.database.write(add_turn,self.c.id,'好呀，已经忙完了',
            [Segment(ja='お疲れさま',zh='辛苦了')],None,self.now.isoformat())
        self.assertIn('reach-out-done',(await self.client.post(path)).text)
        self.assertEqual(len(self.model.prompts),2)
        self.assertEqual(await visits.repository.attempts(self.user.id,night),3)
        self.assertEqual((await self.client.get('/api/account')).json()['visit_outreach_mode'],'tonight')

    async def test_terminal_invitation_reconciles_on_restart_without_retry(self):
        from eidolon.visit_outreach.repository import _prepare
        from eidolon.visit_outreach.repository import _write
        from eidolon.visit_outreach.policy import night_key
        from zoneinfo import ZoneInfo
        import time
        visits=self.backend.visits
        night=night_key(self.now.astimezone(ZoneInfo('Asia/Shanghai')))
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        period=await visits.period(self.c,night)
        await self.backend.database.write(_write,dict(key=(self.user.id,self.c.character_id,period),
            conversation=self.c.id,contact=(await visits.host.last_contact(self.user.id,self.c.character_id)).isoformat(),
            state='preparing',calls=1,created=time.time()))
        await self.backend.database.write(_prepare)
        self.assertEqual((await self.client.post('/api/visit-outreach/'+self.c.id)).text,'')
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'off'})
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        self.assertEqual(await visits.period(self.c,night),period)
        self.assertEqual((await self.client.post('/api/visit-outreach/'+self.c.id)).text,'')
        self.assertEqual((await self.client.get('/api/account')).json()['visit_outreach_mode'],'tonight')
        self.assertEqual(self.model.prompts,[])

    async def test_invitation_reselection_and_parallel_claim_are_idempotent(self):
        from types import SimpleNamespace
        from eidolon.visit_outreach.policy import night_key
        from zoneinfo import ZoneInfo
        visits=self.backend.visits
        now=self.now.astimezone(ZoneInfo('Asia/Shanghai'))
        night=night_key(now)
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        first=await visits.period(self.c,night)
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        self.assertEqual(await visits.period(self.c,night),first)
        other=await self.store.create_conversation(self.user.id,list(self.backend.characters)[1])
        await self.backend.database.write(add_turn,other.id,'你好',[Segment(ja='うん',zh='嗯')])
        await self.backend.database.write(lambda db:db.execute('UPDATE messages SET created_at=?',
            ((self.now-timedelta(minutes=1)).isoformat(),)))
        gate=asyncio.Event()
        original=visits.real_generate
        async def held(c,delta,requested):
            await gate.wait()
            return await original(c,delta,requested)
        visits.real_generate=held
        config=SimpleNamespace(first_body_delay=0,typing_lead=0)
        (job,_),(blocked,_) = await asyncio.gather(visits.start_or_resume(self.c,now,config),visits.start_or_resume(other,now,config))
        winner=job or blocked
        self.assertEqual(sum(j is not None for j in (job,blocked)),1)
        winning_c=self.c if job else other
        resumed,_=await visits.start_or_resume(winning_c,now,config)
        self.assertIs(resumed,winner)
        winner['subscribers']=1
        gate.set()
        await winner['task']
        self.assertEqual(len(self.model.prompts),1)
        self.assertEqual((await self.client.get('/api/account')).json()['visit_outreach_mode'],'tonight')
        await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
        self.assertEqual(await visits.period(winning_c,night),winner['key'][2])
        # A different role may prepare sequentially within the same enabled night.
        self.model._replies.append(['<opening type="encounter"/><ja>こんばんは</ja><zh>晚上好</zh>'])
        losing_c=other if job else self.c
        next_job,reason=await visits.start_or_resume(losing_c,now,config)
        self.assertIsNotNone(next_job,reason)
        next_job['subscribers']=1
        await next_job['task']
        self.assertEqual(len(self.model.prompts),2)

    async def disconnected_request(self, reply, delay=0):
        from types import SimpleNamespace
        from zoneinfo import ZoneInfo
        gate = asyncio.Event()
        self.model._replies[:] = [[gate, reply]]
        await self.client.put('/api/account/visit-outreach-mode', json={'mode':'tonight'})
        visits = self.backend.visits
        job, reason = await visits.start_or_resume(self.c,
            self.now.astimezone(ZoneInfo('Asia/Shanghai')),
            SimpleNamespace(first_body_delay=delay, typing_lead=0))
        self.assertIsNotNone(job, reason)
        events = visits.events(job)
        listener = asyncio.create_task(anext(events))
        async with asyncio.timeout(3):
            while not self.model.prompts or not job['subscribers']:
                await asyncio.sleep(.01)
        self.assertNotIn('typing_at', job)
        listener.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await listener
        await events.aclose()
        self.assertEqual(job['subscribers'], 0)
        return job, gate

    async def test_disconnect_before_typing_saves_once_without_return(self):
        import time
        job, gate = await self.disconnected_request('<ja>こんばんは</ja><zh>晚上好</zh>', .2)
        gate.set()
        await asyncio.wait_for(asyncio.shield(job['task']), 3)
        self.assertEqual(job['state'], 'done')
        self.assertGreaterEqual(time.time() - job['created'], .2)
        self.assertEqual(job['message']['created_at'], job['typing_at'])
        sent = job['message']['created_at']
        self.now += timedelta(hours=1)
        self.assertIn('recorded', (await self.client.post('/api/visit-outreach/'+self.c.id)).text)
        self.assertEqual(job['message']['created_at'], sent)
        self.assertEqual(len(self.model.prompts), 1)

    async def test_disconnect_before_typing_still_yields_to_user_message(self):
        job, gate = await self.disconnected_request('<ja>こんばんは</ja><zh>晚上好</zh>')
        await self.backend.database.write(add_turn, self.c.id, '已经忙完了',
            [Segment(ja='うん', zh='嗯')], None, self.now.isoformat())
        gate.set()
        await asyncio.wait_for(asyncio.shield(job['task']), 3)
        self.assertEqual(job['state'], 'yielded')
        self.assertEqual(job['reason'], 'user_spoke')
        self.assertIsNone(job['message'])

    async def test_disconnect_before_typing_keeps_model_silence(self):
        job, gate = await self.disconnected_request('<silent/>')
        gate.set()
        await asyncio.wait_for(asyncio.shield(job['task']), 3)
        self.assertEqual(job['state'], 'silent')
        self.assertNotIn('typing_at', job)
        self.assertIsNone(job['message'])

    async def test_generator_is_independent_and_keeps_schedule_context(self):
        from eidolon.visit_outreach.context import LegacyContext, V020Context
        from eidolon.visit_outreach.generator import build_prompt
        from eidolon.schedule import HerSchedule, HerJob, Job
        from zoneinfo import ZoneInfo
        local=self.now.astimezone(ZoneInfo('Asia/Shanghai'))
        week=(local-timedelta(days=local.weekday())).date()
        job=Job('visit-test',self.c.character_id,'夜间声乐练习','准备明天的演出','practice',1,None)
        # A real schedule formatter sees a currently ongoing evening job.
        jobs=HerSchedule(local,week,[HerJob(job,week,local.weekday(),2)])
        async def hers(*args): return jobs
        with patch.object(self.backend.schedules,'hers',hers), patch.object(
                self.backend.engine,'reach_out',side_effect=AssertionError('Must not call Engine')):
            ctx=await V020Context(self.backend).prepare(self.c)
            prompt=build_prompt(ctx,(),False)
            self.assertIn('夜间声乐练习',str(prompt))
            self.assertIn('现在（晚上',str(prompt))
            await self.client.put('/api/account/visit-outreach-mode',json={'mode':'tonight'})
            response=await self.client.post('/api/visit-outreach/'+self.c.id)
            self.assertIn('reach-out-done',response.text)
            self.assertEqual(len(self.model.prompts),1)
            self.assertIn('夜间声乐练习',str(self.model.prompts[0]))
        # Legacy hosts need no schedule or calendar implementation.
        with patch.object(self.backend.schedules,'hers',side_effect=AssertionError('No schedule access')):
            legacy=await LegacyContext(self.backend).prepare(self.c)
            self.assertNotIn('夜间声乐练习',str(build_prompt(legacy,(),False)))

    async def test_schedule_read_precedes_affinity_snapshot(self):
        from eidolon.visit_outreach.context import V020Context
        adapter=V020Context(self.backend)
        events=[]
        async def hers(*args): events.append('schedule'); return None
        original=adapter.snapshot
        def snapshot(*args): events.append('snapshot'); return original(*args)
        with patch.object(self.backend.schedules,'hers',hers), patch.object(adapter,'snapshot',snapshot):
            await adapter.prepare(self.c)
        self.assertEqual(events,['schedule','snapshot'])

