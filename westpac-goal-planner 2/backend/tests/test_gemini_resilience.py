"""Retry/resume tests use mock providers and clocks; never a real key or real backoff."""
from copy import deepcopy
from datetime import datetime,timezone
from email.utils import format_datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch,MagicMock
import httpx
from google.genai import errors,types
from planning.agent import GeminiPlanner
from planning.gemini import GeminiTransport,PlannerError,GenerationRetryPolicy,retry_after
from planning.checkpoint import pack,unpack
from planning.agent_tools import PlanningTools
from planning.planner_service import PlannerService
from tests.test_gemini_corrections import Scripted,proposal
from tests.test_gemini_planner import MockGemini,output,tool_response
from tests.test_gemini_retries import Clock
from planning.demo import demo_contexts,AS_OF


class AgentResilienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.ctx=demo_contexts()['normal']
    def run_agent(self,t,clock=None,policy=None,**kwargs):
        clock=clock or Clock()
        agent=GeminiPlanner(t,clock=clock,sleeper=clock.sleep,jitter=lambda low,high:high,
                            retry_policy=policy or GenerationRetryPolicy(4,20,5,40),**kwargs)
        draft=agent.run(self.ctx,goal_ref='goal-1',question='Assess')
        return draft,agent,clock

    def test_retry_after_seconds_date_invalid_and_zero_quota(self):
        self.assertEqual(retry_after(httpx.Response(503,headers={'Retry-After':'12'})),12)
        stamp=datetime.fromtimestamp(1000,tz=timezone.utc)
        self.assertEqual(retry_after(httpx.Response(503,headers={'Retry-After':format_datetime(stamp)}),now=990),10)
        for value in ['-1','nan','inf','not a date']:
            self.assertIsNone(retry_after(httpx.Response(503,headers={'Retry-After':value})))
        with patch.dict(os.environ,{'GEMINI_API_KEY':'test-key'}):
            for zero in [True,False]:
                t=GeminiTransport(env_file=None);t.client=MagicMock()
                details={'error':{'message':'Quota exceeded, limit: 0' if zero else 'Temporary rate limit'}}
                t.client.models.generate_content.side_effect=errors.APIError(429,details,response=httpx.Response(429,headers={'Retry-After':'10'}))
                with self.assertRaises(PlannerError) as cm:t.generate([],types.GenerateContentConfig(),1000)
                self.assertEqual(cm.exception.retryable,not zero)
                self.assertTrue(cm.exception.resume_allowed) # Exhaustion still retains the checkpoint for explicit resumption.
                self.assertEqual(cm.exception.retry_after_seconds,10)

    def test_retry_guidance_and_progress_preserve_correction(self):
        t=Scripted([proposal(600,True),PlannerError('planner_provider_error','Busy',502,upstream_status=503,retry_after_seconds=7),proposal(600)])
        progress=[];clock=Clock()
        agent=GeminiPlanner(t,clock=clock,sleeper=clock.sleep,jitter=lambda a,b:b)
        draft=agent.run(self.ctx,goal_ref='goal-1',question='Assess',on_progress=progress.append)
        self.assertEqual(clock.delays,[7]);self.assertEqual(draft['metadata']['correction_attempts'],1)
        self.assertEqual(draft['metadata']['retry_attempts'],1)
        self.assertIn('waiting',[p['status'] for p in progress]);self.assertTrue(draft['metadata']['correction_succeeded'])
        self.assertEqual(t.calls[-1][0],t.calls[-2][0])

    def test_default_backoff_four_delays_and_finite_exhaustion(self):
        t=Scripted([PlannerError('planner_provider_error','Busy',502,upstream_status=503)]*5)
        clock=Clock();a=GeminiPlanner(t,clock=clock,sleeper=clock.sleep,jitter=lambda a,b:b)
        with self.assertRaises(PlannerError) as cm:a.run(self.ctx,goal_ref='goal-1',question='Assess')
        self.assertEqual(cm.exception.upstream_status,503)
        self.assertEqual(clock.delays,[5,10,20,40]);self.assertEqual(len(t.calls),6)
        self.assertEqual(a.last_metrics['retry_attempts'],4)

    def test_checkpoint_resumes_correction_without_calculation_replay(self):
        first=Scripted([proposal(600,True),PlannerError('planner_timeout','Busy',504,upstream_status=504)])
        agent=GeminiPlanner(first,retry_policy=GenerationRetryPolicy(max_retries=0))
        original=PlanningTools.calculate_strategy
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=original) as calc:
            with self.assertRaises(PlannerError):agent.run(self.ctx,goal_ref='goal-1',question='Assess')
            checkpoint=agent.last_checkpoint
            state=unpack(checkpoint['state']);self.assertEqual(state['pending']['stage'],'structured_correction')
            second=Scripted([proposal(600)])
            resumed=GeminiPlanner(second,retry_policy=GenerationRetryPolicy(max_retries=0))
            draft=resumed.run(self.ctx,goal_ref='goal-1',question='Assess',checkpoint=checkpoint)
        self.assertEqual(calc.call_count,1);self.assertEqual(len(second.calls),1)
        self.assertEqual(draft['metadata']['correction_attempts'],1)
        self.assertTrue(draft['metadata']['correction_succeeded']);self.assertFalse(draft['activation_allowed'])
        self.assertEqual(draft['metadata']['logical_model_calls'],3)
        clock=Clock()
        class Late(Scripted):
            def generate(self,*args):
                response=super().generate(*args)
                if not args[1].tools:clock.now+=2
                return response
        late=GeminiPlanner(Late([proposal(600)]),clock=clock,timeout_seconds=1)
        with self.assertRaises(PlannerError):late.run(self.ctx,goal_ref='goal-1',question='Assess')
        receiver=Scripted([])
        final=GeminiPlanner(receiver).run(self.ctx,goal_ref='goal-1',question='Assess',checkpoint=late.last_checkpoint)
        self.assertEqual(receiver.calls,[]);self.assertEqual(final['status'],'draft_unverified')

    def test_checkpoint_partial_tool_batch_skips_completed_tools_and_keeps_bytes(self):
        class Batch(MockGemini):
            def generate(self,*args):
                response=super().generate(*args)
                if args[1].tools:response.candidates[0].content.parts[0].thought_signature=b'\xff\x00signature'
                return response
        a=GeminiPlanner(Batch());stopped=[False]
        def interrupt(cp):
            state=unpack(cp['state'])
            if state['processed']==1 and not stopped[0]:
                stopped[0]=True;raise PlannerError('planner_provider_error','Simulated process interruption',502,upstream_status=503)
        original=PlanningTools.calculate_strategy
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=original) as calc:
            with self.assertRaises(PlannerError):a.run(self.ctx,goal_ref='goal-1',question='Assess',on_checkpoint=interrupt)
            second=MockGemini();draft=GeminiPlanner(second).run(self.ctx,goal_ref='goal-1',question='Assess',checkpoint=a.last_checkpoint)
        self.assertEqual(calc.call_count,2)
        self.assertEqual(len(second.calls),1)
        self.assertTrue(any(p.thought_signature==b'\xff\x00signature' for c in second.calls[0][0] for p in c.parts))
        self.assertEqual(len(draft['calculations']),2)
        self.assertEqual(unpack(pack({'opaque':['bytes','abc'],'signature':b'\xff'})),{'opaque':['bytes','abc'],'signature':b'\xff'})

    def test_cancellation_during_wait_stops_before_next_call(self):
        t=Scripted([PlannerError('planner_provider_error','Busy',502,upstream_status=503)])
        clock=Clock();cancelled=[False]
        def sleep(delay):clock.sleep(delay);cancelled[0]=True
        a=GeminiPlanner(t,clock=clock,sleeper=sleep)
        with self.assertRaises(PlannerError) as cm:a.run(self.ctx,goal_ref='goal-1',question='Assess',cancelled=lambda:cancelled[0])
        self.assertEqual(cm.exception.code,'planner_cancelled');self.assertEqual(len(t.calls),2)

    def test_long_retry_after_interrupts_instead_of_retrying_early(self):
        t=Scripted([PlannerError('planner_provider_error','Busy',502,upstream_status=503,retry_after_seconds=600)])
        clock=Clock();a=GeminiPlanner(t,clock=clock,sleeper=clock.sleep)
        with self.assertRaises(PlannerError) as cm:a.run(self.ctx,goal_ref='goal-1',question='Assess')
        self.assertEqual(cm.exception.code,'planner_limit');self.assertEqual(clock.delays,[])
        self.assertEqual(cm.exception.retry_after_seconds,600)
        t=Scripted([PlannerError('planner_provider_error','Busy',502,upstream_status=503,retry_after_seconds=150),proposal(600)])
        clock=Clock();draft,_,_=self.run_agent(t,clock=clock)
        self.assertEqual(clock.delays,[150]);self.assertEqual(draft['status'],'draft_unverified')


class PersistedResumeTests(unittest.TestCase):
    def setUp(self):
        from tests import test_gemini_planner as fixture
        fixture.PersistenceTests.setUp(self)
        self.policy=patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'0'});self.policy.start();self.addCleanup(self.policy.stop)
    def ask(self):
        return self.planner.generate(self.bank,self.customer,goal_id='goal-id',question='Assess',request_id='resumable',expected_revision=1)
    def interrupted(self):
        self.planner.transport=Scripted([PlannerError('planner_provider_error','Busy',502,upstream_status=503)])
        with self.assertRaises(PlannerError):self.ask()
        row=self.planner.store.request(self.customer,'resumable');self.assertEqual(row['status'],'interrupted')
        return row
    def test_restart_resume_scope_idempotency_and_no_goal_changes(self):
        before=self.store.read(self.customer,AS_OF);row=self.interrupted()
        second=Scripted([proposal(600)])
        service=PlannerService(self.store,second,cooldown_seconds=0)
        service.retry(self.bank,self.customer,'resumable',expected_revision=1)
        service.jobs[(self.customer,'resumable')].join(10)
        job=service.request_status(self.bank,self.customer,'resumable')
        self.assertEqual(job['status'],'completed',job)
        self.assertEqual(len(second.calls),1)
        self.assertEqual(len(service.store.history(self.customer)),1)
        again=service.generate(self.bank,self.customer,goal_id='goal-id',question='Assess',request_id='resumable',expected_revision=1)
        self.assertEqual(again['draft_id'],job['draft']['draft_id']);self.assertEqual(len(second.calls),1)
        self.assertEqual(self.store.read(self.customer,AS_OF),before)
        with self.assertRaises(ValueError):service.request_status(self.bank,'foreign','resumable')
        with self.assertRaises(ValueError):self.bank.decide_proposal(self.customer,again['draft_id'],'approve',confirmed=True,expected_revision=1)
        stored=json.dumps(row);self.assertNotIn('GEMINI_API_KEY',stored);self.assertNotIn('api_key',stored)
    def test_stale_context_clears_checkpoint_before_network(self):
        from planning.schemas import AccountSnapshot
        self.interrupted();self.raw['accounts']=(AccountSnapshot('cash','transaction',150,AS_OF),)
        second=Scripted([proposal(600)]);service=PlannerService(self.store,second,cooldown_seconds=0)
        service.retry(self.bank,self.customer,'resumable',expected_revision=1)
        service.jobs[(self.customer,'resumable')].join(10)
        record=service.store.request(self.customer,'resumable')
        self.assertEqual(record['status'],'stale');self.assertIsNone(record['checkpoint']);self.assertEqual(second.calls,[])
    def test_user_cancellation_during_provider_call(self):
        entered=Event();release=Event()
        class Blocking(MockGemini):
            def generate(self,*args):
                entered.set();release.wait(5);return super().generate(*args)
        self.planner.transport=Blocking();before=self.store.read(self.customer,AS_OF)
        self.planner.start(self.bank,self.customer,goal_id='goal-id',question='Assess',request_id='cancel',expected_revision=1)
        self.assertTrue(entered.wait(5));self.planner.cancel(self.customer,'cancel');release.set()
        self.planner.jobs[(self.customer,'cancel')].join(10)
        record=self.planner.store.request(self.customer,'cancel')
        self.assertEqual(record['status'],'cancelled');self.assertNotIn('draft',record)
        self.assertEqual(self.store.read(self.customer,AS_OF),before)
    def test_resume_limits_and_retry_after_block_new_calls(self):
        self.interrupted()
        self.planner.store.alter(self.customer,'resumable',lambda r:{**r,'retry_not_before':10**12})
        with self.assertRaises(PlannerError):self.planner.retry(self.bank,self.customer,'resumable',expected_revision=1)
        self.planner.store.alter(self.customer,'resumable',lambda r:{**r,'retry_not_before':0,'resume_count':2})
        with self.assertRaises(PlannerError):self.planner.retry(self.bank,self.customer,'resumable',expected_revision=1)
        self.planner.store.alter(self.customer,'resumable',lambda r:{**r,'resume_count':0})
        self.planner.gate.acquire()
        try:
            self.planner.retry(self.bank,self.customer,'resumable',expected_revision=1)
            self.planner.jobs[(self.customer,'resumable')].join(10)
        finally:self.planner.gate.release()
        record=self.planner.store.request(self.customer,'resumable')
        self.assertEqual(record['status'],'interrupted');self.assertIsNotNone(record['checkpoint'])


class JobApiTests(unittest.TestCase):
    def test_background_status_owner_scope_cancel_and_no_activation(self):
        from api import create_app
        from fastapi.testclient import TestClient
        entered=Event();release=Event()
        class Blocking(MockGemini):
            def generate(self,*args):
                entered.set();release.wait(30);return super().generate(*args)
        with TemporaryDirectory() as folder:
            transport=Blocking();app=create_app(Path(folder)/'jobs.sqlite',planner_transport=transport)
            with TestClient(app) as client:
                client.post('/api/session',json={'customer_id':'u003','demo_acknowledged':True})
                view=client.get('/api/overview').json()
                view=client.post('/api/scenario',headers={'X-Context-Scope':view['scope_version']},json={'scenario':'normal'}).json()
                headers={'X-Context-Scope':view['scope_version']}
                customer=next(iter(app.state.sessions.values())).sandbox
                before=app.state.store.read(customer,AS_OF)
                body={'goal_id':'demo-holiday','question':'Assess','request_id':'job-api','expected_revision':view['revision'],'background':True}
                response=client.post('/api/planner/assess',headers=headers,json=body)
                self.assertEqual(response.status_code,202,response.text)
                try:
                    self.assertTrue(entered.wait(30))
                    job=client.get('/api/planner/requests/job-api',headers=headers)
                    self.assertEqual(job.status_code,200)
                    self.assertNotIn('checkpoint',job.json());self.assertNotIn('contents',job.json())
                    duplicate=client.post('/api/planner/assess',headers=headers,json=body)
                    self.assertEqual(duplicate.status_code,202)
                    self.assertEqual(client.post('/api/planner/requests/job-api/cancel').status_code,409)
                    with TestClient(app) as foreign:
                        foreign.post('/api/session',json={'customer_id':'u004','demo_acknowledged':True})
                        other=foreign.get('/api/overview').json()
                        self.assertEqual(foreign.get('/api/planner/requests/job-api',headers={'X-Context-Scope':other['scope_version']}).status_code,400)
                    self.assertEqual(client.post('/api/planner/requests/job-api/cancel',headers=headers).status_code,200)
                finally:release.set()
                app.state.planner.jobs[(customer,'job-api')].join(30)
                job=client.get('/api/planner/requests/job-api',headers=headers).json()
                self.assertEqual(job['status'],'cancelled',job)
                self.assertNotIn('draft',job)
                self.assertEqual(app.state.store.read(customer,AS_OF),before)
                self.assertEqual(len(transport.calls),1)
