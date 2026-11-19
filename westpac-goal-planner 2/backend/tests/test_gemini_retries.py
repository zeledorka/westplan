"""Bounded generation-level retries. All model responses here are explicit test doubles."""
from copy import deepcopy
import os
import unittest
from unittest.mock import MagicMock,patch
from google.genai import types,errors
from planning.agent import GeminiPlanner
from planning.agent_tools import PlanningTools
from planning.demo import demo_contexts
from planning.gemini import GeminiTransport,PlannerError,GenerationRetryPolicy
from tests.test_gemini_planner import MockGemini

class Clock:
    def __init__(self):self.now=0;self.delays=[]
    def __call__(self):return self.now
    def sleep(self,delay):self.delays.append(delay);self.now+=delay

class TransientFinal(MockGemini):
    def __init__(self,statuses):super().__init__();self.statuses=list(statuses);self.attempts=[]
    def generate(self,contents,config,timeout_ms):
        final=config.response_mime_type=='application/json'
        self.attempts.append({'final':final,'contents':deepcopy(contents),'timeout_ms':timeout_ms})
        if final and self.statuses:
            status=self.statuses.pop(0)
            raise PlannerError('planner_provider_error','Sanitised transient error',502,upstream_status=status)
        return super().generate(contents,config,timeout_ms)

class RetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.context=demo_contexts()['normal']
    def agent(self,transport,policy=None,deadline=100,clock=None,jitter=None):
        clock=clock or Clock()
        return GeminiPlanner(transport,retry_policy=policy or GenerationRetryPolicy(2,6,1,4),timeout_seconds=deadline,
            clock=clock,sleeper=clock.sleep,jitter=jitter or (lambda low,high:high)),clock
    def run_agent(self,agent):return agent.run(self.context,goal_ref='goal-1',question='Assess the goal')

    def test_final_retry_preserves_calculations_history_and_does_not_replay_tools(self):
        transport=TransientFinal([503,504]);agent,clock=self.agent(transport)
        original=PlanningTools.calculate_strategy
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=original) as calculate:
            draft=self.run_agent(agent)
        self.assertEqual(calculate.call_count,2)
        self.assertEqual(draft['metadata']['model_calls'],4)
        self.assertEqual(draft['metadata']['logical_model_calls'],2)
        self.assertEqual(draft['metadata']['retry_attempts'],2)
        self.assertEqual(draft['metadata']['tool_calls'],3)
        self.assertEqual(clock.delays,[1,2])
        finals=[a for a in transport.attempts if a['final']]
        self.assertEqual(len(finals),3)
        self.assertEqual(finals[0]['contents'],finals[1]['contents'])
        self.assertEqual(finals[1]['contents'],finals[2]['contents'])
        self.assertTrue(any(p.function_response and 'plans' in p.function_response.response for c in finals[0]['contents'] for p in c.parts))
        self.assertEqual(draft['status'],'draft_unverified');self.assertFalse(draft['activation_allowed'])
        self.assertEqual(draft['calculations']['calculation-1']['plans'][0]['contribution'],600)

    def test_retry_is_capped_per_call_and_can_be_disabled(self):
        for allowed in (0,1):
            transport=TransientFinal([503]*9)
            agent,clock=self.agent(transport,GenerationRetryPolicy(max_retries=allowed,max_attempts=8))
            with self.assertRaises(PlannerError) as cm:self.run_agent(agent)
            self.assertEqual(cm.exception.upstream_status,503)
            self.assertEqual(len(transport.attempts),2+allowed)
            self.assertEqual(agent.last_metrics['retry_attempts'],allowed)
            self.assertEqual(len(clock.delays),allowed)

    def test_global_attempt_budget_spans_tool_and_final_generations(self):
        transport=TransientFinal([503]*9)
        agent,clock=self.agent(transport,GenerationRetryPolicy(max_retries=3,max_attempts=3))
        with self.assertRaises(PlannerError) as cm:self.run_agent(agent)
        self.assertEqual(cm.exception.code,'planner_limit')
        self.assertEqual(len(transport.attempts),3)
        self.assertEqual(agent.last_metrics['logical_model_calls'],2)
        self.assertEqual(agent.last_metrics['retry_attempts'],1)
        self.assertEqual(len(clock.delays),1)

    def test_non_transient_errors_and_local_timeouts_are_not_retried(self):
        for status in (400,401,403,404,408,429,500,502,None):
            with self.subTest(status=status):
                transport=TransientFinal([status]);agent,clock=self.agent(transport)
                with self.assertRaises(PlannerError):self.run_agent(agent)
                self.assertEqual(len(transport.attempts),2)
                self.assertEqual(clock.delays,[])

    def test_backoff_is_exponential_jittered_and_capped(self):
        transport=TransientFinal([503,503,504])
        bounds=[]
        def jitter(low,high):bounds.append((low,high));return (low+high)/2
        agent,clock=self.agent(transport,GenerationRetryPolicy(max_retries=3,base_seconds=1,max_seconds=1.5),jitter=jitter)
        self.run_agent(agent)
        self.assertEqual(bounds,[(.8,1),(1.2000000000000002,1.5),(1.2000000000000002,1.5)])
        self.assertEqual(clock.delays,[.9,1.35,1.35])

    def test_deadline_prevents_backoff_when_no_attempt_can_fit(self):
        clock=Clock()
        class SlowFailure(TransientFinal):
            def generate(self,*args):
                if args[1].response_mime_type=='application/json':clock.now+=1.6
                return super().generate(*args)
        transport=SlowFailure([503]);agent,_=self.agent(transport,deadline=2,clock=clock)
        with self.assertRaises(PlannerError) as cm:self.run_agent(agent)
        self.assertEqual(cm.exception.code,'planner_limit')
        self.assertEqual(len(transport.attempts),2);self.assertEqual(clock.delays,[])

    def test_each_retry_timeout_uses_remaining_shared_deadline(self):
        clock=Clock()
        class Timed(TransientFinal):
            def generate(self,*args):
                if args[1].response_mime_type=='application/json':clock.now+=.2
                return super().generate(*args)
        transport=Timed([504]);agent,_=self.agent(transport,deadline=3,clock=clock)
        self.run_agent(agent)
        self.assertEqual(transport.attempts[0]['timeout_ms'],3000)
        self.assertLess(transport.attempts[-1]['timeout_ms'],transport.attempts[-2]['timeout_ms'])
        self.assertEqual(agent.last_metrics['retry_attempts'],1)

    def test_late_success_is_not_accepted_after_execution_deadline(self):
        clock=Clock()
        class Late(MockGemini):
            def generate(self,*args):clock.now+=2;return super().generate(*args)
        agent,_=self.agent(Late(),deadline=1,clock=clock)
        with self.assertRaises(PlannerError) as cm:self.run_agent(agent)
        self.assertEqual(cm.exception.code,'planner_limit')
        self.assertEqual(agent.last_metrics['model_calls'],1)
        self.assertEqual(agent.last_metrics['tool_calls'],0)

    def test_retry_configuration_is_validated_and_bounded(self):
        with patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'1','GEMINI_MAX_GENERATION_ATTEMPTS':'5','GEMINI_RETRY_BASE_SECONDS':'.5','GEMINI_RETRY_MAX_SECONDS':'2'}):
            policy=GenerationRetryPolicy.from_environment()
            self.assertEqual(policy,GenerationRetryPolicy(1,5,.5,2))
        for kwargs in ({'max_retries':7},{'max_attempts':33},{'base_seconds':float('nan')},{'max_seconds':121},{'max_attempts':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(PlannerError):GenerationRetryPolicy(**kwargs)
        with patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'invalid'}):
            with self.assertRaises(PlannerError):GenerationRetryPolicy.from_environment()

    def test_sdk_retries_stay_disabled_and_only_upstream_503_504_are_tagged(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'test-only-key'}):
            for status in (400,503,504):
                transport=GeminiTransport(env_file=None);transport.client=MagicMock()
                transport.client.models.generate_content.side_effect=errors.APIError(status,{'error':{'message':'private provider detail'}})
                config=types.GenerateContentConfig()
                with self.assertRaises(PlannerError) as cm:transport.generate([],config,1000)
                self.assertEqual(cm.exception.upstream_status,status)
                self.assertEqual(transport.client.models.generate_content.call_count,1)
                self.assertEqual(config.http_options.retry_options.attempts,1)
                self.assertNotIn('private provider detail',cm.exception.message)
        from google import genai
        with genai.Client(api_key='test-only-key',http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))) as sdk:
            self.assertEqual(sdk._api_client._retry.stop.max_attempt_number,1)

    def test_failure_metrics_persist_and_invalid_configuration_releases_request_gate(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from datetime import date
        from planning.store import DemoStore
        from planning.service import BankingService
        from planning.planner_service import PlannerService
        from planning.schemas import AccountSnapshot,Goal
        day=date(2026,9,30)
        with TemporaryDirectory() as folder:
            store=DemoStore(Path(folder)/'retry.sqlite')
            raw={'txns':[],'as_of':day,'accounts':(AccountSnapshot('cash','transaction',2000,day),)}
            bank=BankingService(store,lambda customer:raw)
            from tests.test_gemini_planner import seed_legacy_goal
            seed_legacy_goal(bank,'customer',Goal('goal','holiday',6000,0,date(2027,5,31)),0)
            before=store.read('customer',day)
            transport=TransientFinal([503]*10);service=PlannerService(store,transport,cooldown_seconds=0)
            def ask(request_id):
                return service.generate(bank,'customer',goal_id='goal',question='Assess',request_id=request_id,expected_revision=1)
            with patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'1','GEMINI_RETRY_BASE_SECONDS':'.1','GEMINI_RETRY_MAX_SECONDS':'.1'}):
                with self.assertRaises(PlannerError):ask('retry-failure')
            record=service.store.request('customer','retry-failure')
            self.assertEqual(record['metadata']['model_calls'],3)
            self.assertEqual(record['metadata']['retry_attempts'],1)
            with patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'invalid'}):
                with self.assertRaises(PlannerError) as cm:ask('configuration-failure')
                self.assertEqual(cm.exception.code,'planner_configuration')
            self.assertTrue(service.gate.acquire(blocking=False));service.gate.release()
            self.assertEqual(store.read('customer',day),before)
