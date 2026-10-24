"""Real upstream async Database/Store/Engine, temporary data, no external providers."""
import asyncio
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from eidolon import data_access
from eidolon.data_access import Database
from eidolon.store import Store, ReachOut, add_turn
from eidolon.engine import ConversationEngine
from eidolon.characters import load_characters
from eidolon.reply_format import Segment
from eidolon.visit_outreach.host import EidolonHost
from eidolon.visit_outreach.context import LegacyContext
from eidolon.visit_outreach.generator import VisitGenerator
from eidolon.visit_outreach.repository import Repository
from eidolon.visit_outreach.service import VisitService

data_access.ON_THE_LOOP_RAISES = True

class Model:
    def __init__(self, text='<opening type="topic"/><ja>こんばんは</ja><zh>晚上好</zh>'):
        self.text, self.prompts = text, []
    async def stream(self, prompt):
        self.prompts.append(prompt)
        for text in (self.text[:12], self.text[12:]):
            yield text

class Memory:
    async def for_reaching_out(self, *args):
        return '今晚可能加班到十一点。'

class AsyncHostTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='async-visit-')
        # prepare runs during host construction, before serving requests.
        self.db = Database(Path(self.temp.name)/'test.db')
        self.store = Store(self.db)
        self.characters = load_characters(Path('/characters/characters.toml'))
        self.now = datetime(2026,10,22,23,tzinfo=UTC)
        self.host = EidolonHost(self.store, SimpleNamespace(in_call=lambda _:False), lambda:self.now,database=self.db)
        self.service = VisitService(Repository(self.db), self.host, lambda:self.now)
        self.service.message_clock = lambda:self.now
        self.memory = Memory()
        self.engine = ConversationEngine(self.db,self.store,self.characters,{},self.memory,None,lambda:self.now)
        self.host.backend = SimpleNamespace(database=self.db,store=self.store,characters=self.characters,
            gift_catalog={},memories=self.memory,affinities=SimpleNamespace(on=False),world=None,
            clock=lambda:self.now,config=SimpleNamespace(history_turns=10,
                lease=lambda:SimpleNamespace(keys=SimpleNamespace(chat_llm=self.model),release=lambda:None)))
        self.user = await self.store.create_user('async-test')
        self.c = await self.conversation(next(iter(self.characters)))
        self.model = Model()
        self.config = SimpleNamespace(first_body_delay=0, typing_lead=0)
        self.service.real_generate = VisitGenerator(LegacyContext(self.host.backend),self.host,self.service)

    async def conversation(self, character):
        c = await self.store.create_conversation(self.user.id,character)
        await self.db.write(add_turn,c.id,'今天可能忙到十一点',[Segment(ja='うん',zh='嗯')])
        await self.db.write(lambda db:db.execute('UPDATE messages SET created_at=? WHERE conversation_id=?',
                           ((self.now-timedelta(hours=2)).isoformat(),c.id)))
        return c

    async def asyncTearDown(self):
        await self.service.aclose()
        await self.db.close()
        self.temp.cleanup()

    async def create(self, c=None):
        c=c or self.c
        return await self.service.create(c,'2026-10-22',await self.host.last_contact(c.user_id,c.character_id),
                                         self.config,self.now+timedelta(hours=6))

    async def finish(self, job):
        events=[event async for event in self.service.events(job)]
        await job['task']
        return events

    async def reaches(self):
        return [x for x in await self.store.record(self.c.id) if isinstance(x,ReachOut)]

    async def test_native_stream_timestamp_and_refresh_share_one_generation(self):
        jobs=await asyncio.gather(*(self.create() for _ in range(20)))
        self.assertTrue(all(j is jobs[0] for j in jobs))
        events=await self.finish(jobs[0])
        # A fast model may now finish before any browser subscribes.
        self.assertTrue(any('reach-out-done' in event or 'recorded' in event for event in events))
        self.assertEqual(len(self.model.prompts),1)
        self.assertEqual(len(await self.reaches()),1)
        self.assertEqual((await self.reaches())[0].created_at,self.now.isoformat())
        self.assertIs(await self.create(),jobs[0])

    async def test_inspection_and_reservation_race_share_canonical_job(self):
        inspection = await self.service.inspect_visit(self.c,self.now)
        self.assertIsNotNone(inspection['seconds'])
        entered,release=asyncio.Event(),asyncio.Event()
        original=self.service.repository.reserve
        async def paused(job):
            result=await original(job)
            entered.set()
            await release.wait()
            return result
        with patch.object(self.service.repository,'reserve',paused):
            first=asyncio.create_task(self.service.start_or_resume(self.c,self.now,self.config))
            await asyncio.wait_for(entered.wait(),5)
            restored,reason=await self.service.start_or_resume(self.c,self.now,self.config)
            self.assertIsNone(reason)
            release.set()
            created,_=await first
        self.assertIs(created,restored)
        await self.finish(restored)
        self.assertEqual(len(self.model.prompts),1)

    async def test_cross_character_cap_is_atomic(self):
        conversations=[self.c]+[await self.conversation(c) for c in list(self.characters)[1:6]]
        jobs=await asyncio.gather(*(self.create(c) for c in conversations))
        self.assertEqual(sum(job is not None for job in jobs),3)
        self.assertEqual(len(await self.service.status(self.user.id)),3)

    async def test_writer_wait_does_not_block_loop(self):
        entered,release=threading.Event(),threading.Event()
        def blocking(db):
            entered.set()
            release.wait(3)
        task=asyncio.create_task(self.db.write(blocking))
        await asyncio.to_thread(entered.wait,2)
        queued=asyncio.create_task(self.service.opportunity(self.c,'night','trust'))
        try:
            await asyncio.sleep(.03)
            self.assertFalse(queued.done())
            # A native read continues while writer work is held.
            self.assertIsNotNone(await self.store.get_conversation(self.c.id))
        finally:
            release.set()
            await task
            await queued

    async def test_cancel_http_waiter_keeps_reserved_job(self):
        entered,release=asyncio.Event(),asyncio.Event()
        original=self.service.repository.reserve
        async def paused(job):
            result=await original(job)
            entered.set()
            await release.wait()
            return result
        with patch.object(self.service.repository,'reserve',paused):
            request=asyncio.create_task(self.create())
            await asyncio.wait_for(entered.wait(), 5)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError): await request
            release.set()
            await asyncio.gather(*self.service._reservations)
        job=await self.create()
        await self.finish(job)
        self.assertEqual(len(self.model.prompts),1)
        self.assertEqual(len(await self.reaches()),1)

    async def test_cancel_during_store_commit_records_success(self):
        entered,release=asyncio.Event(),asyncio.Event()
        original=self.host.save
        async def paused(*args):
            message=await original(*args)
            entered.set()
            await release.wait()
            return message
        with patch.object(self.host,'save',paused):
            job=await self.create()
            reader=asyncio.create_task(self.finish(job))
            await asyncio.wait_for(entered.wait(), 5)
            job['task'].cancel()
            release.set()
            await reader
        self.assertEqual(job['state'],'done')
        self.assertEqual(len(await self.reaches()),1)
        self.assertEqual((await self.service.status(self.user.id))[0]['state'],'done')

    async def test_delete_user_and_late_write_do_not_resurrect(self):
        job=await self.create()
        await self.service.opportunity(self.c,'night','trust')
        await self.store.delete_user(self.user.id)
        self.service.forget_user(self.user.id)
        await asyncio.gather(job['task'],return_exceptions=True)
        self.assertFalse(await self.service.write(job))
        self.assertEqual(await self.service.status(self.user.id),[])
        self.assertEqual(await self.service.opportunity_history(self.user.id),[])
        self.assertFalse((await self.service.opportunity(self.c,'night','trust'))['eligible'])

    async def test_user_speaks_during_retrieval_yields_without_model_call(self):
        async def memory(*args):
            await self.db.write(add_turn,self.c.id,'我回来啦',[Segment(ja='うん',zh='嗯')])
            return ''
        with patch.object(self.memory,'for_reaching_out',memory):
            job=await self.create()
            await job['task']
        self.assertEqual(job['state'],'yielded')
        self.assertEqual(self.model.prompts,[])

    async def test_silence_and_ordinary_outreach_stay_distinct(self):
        silent=Model('<silent/>')
        self.model=silent
        async def noop(*args): pass
        self.assertEqual(await self.service.real_generate(self.c,noop,noop),[])
        ordinary=Model('<ja>こんにちは</ja><zh>你好</zh>')
        said=await self.engine.reach_out(self.c,ordinary)
        self.assertEqual(said[0].zh,'你好')
        self.assertNotIn('opening type',str(ordinary.prompts[0]))
        self.assertIn('你想先给他发几句',str(ordinary.prompts[0]))

if __name__=='__main__': unittest.main()

