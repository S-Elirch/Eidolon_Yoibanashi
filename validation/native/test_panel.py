import unittest
from datetime import datetime
from pathlib import Path
import test_runtime
from panel import Controls, TestClock, install


class PanelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = test_runtime.NativeRuntimeTests.asyncSetUp
    asyncTearDown = test_runtime.NativeRuntimeTests.asyncTearDown

    def attach(self):
        clock = TestClock(Path(self.temp.name)/'clock.json')
        clock.save(Controls(local=datetime(2026,10,24,23)))
        install(self.app, self.backend, clock)
        return clock

    async def test_panel_and_authenticated_state(self):
        self.attach()
        self.assertEqual((await self.client.get('/api/dev2/panel')).status_code,200)
        state=(await self.client.get('/api/dev4/state')).json()
        self.assertEqual(state['night'],'2026-10-24')
        self.assertEqual(state['user'],'runtime-test')
        self.client.cookies.clear()
        self.assertEqual((await self.client.get('/api/dev4/state')).status_code,401)
        self.assertEqual(self.model.prompts,[])

    async def test_jump_pacing_and_busy_guard(self):
        clock=self.attach()
        r=await self.client.post('/api/dev4/controls',json={'local':'2026-10-25T23:00:00','paused':True,'dwell_tier':2,'first_body_delay':12})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(self.backend.visits.presentation.first_body_delay,12)
        self.assertEqual(self.backend.visits.schedule(self.c,clock()).probabilities,(0,0,100))
        r=await self.client.post('/api/dev4/jump',json={'action':'1d'})
        self.assertEqual(r.json()['night'],'2026-10-26')
        self.backend.visits.pending.add(self.c.id)
        self.assertEqual((await self.client.post('/api/dev4/jump',json={'action':'1d'})).status_code,409)
        self.backend.visits.pending.clear()
        self.assertEqual(TestClock(clock.path)().date(),clock().date())
        self.assertEqual(self.model.prompts,[])

    async def test_reset_preserves_source(self):
        self.attach()
        def counts(db):
            return db.execute('SELECT count(*) FROM messages').fetchone()[0]
        before=await self.backend.database.read(counts)
        r=await self.client.post('/api/dev4/reset',json={})
        self.assertEqual(r.status_code,200,r.text)
        state=(await self.client.get('/api/dev4/state')).json()
        self.assertTrue(state['user'].startswith('dev4-round-'))
        self.assertEqual(state['opportunities'],[])
        self.assertEqual(await self.backend.database.read(counts),before*2)
        self.assertIsNotNone(await self.store.get_conversation(self.c.id))
        self.assertEqual(self.model.prompts,[])
