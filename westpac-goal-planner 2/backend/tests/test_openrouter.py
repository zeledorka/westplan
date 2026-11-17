"""Offline OpenRouter protocol, safety, pacing and recovery through the real planner."""
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch

import httpx
from google.genai import types

from planning.agent import GeminiPlanner, private_text
from planning.agent_tools import PlanningTools
from planning.checkpoint import pack, unpack
from planning.demo import demo_contexts
from planning.gemini import GenerationRetryPolicy, PlannerError
from planning.openrouter import OpenRouterTransport, strict_json
from planning.providers import configured_transport
from planning.safety import assess_draft
from tests.test_gemini_planner import output, MockGemini
from tests.test_gemini_retries import Clock


def endpoint(**changes):
    result = {'tag': 'nvidia', 'context_length': 1000000, 'max_completion_tokens': 65536,
              'pricing': {'prompt': '0', 'completion': '0'},
              'supported_parameters': ['tools', 'tool_choice', 'max_tokens', 'reasoning', 'reasoning_effort', 'temperature'],
              'supports_tool_choice': {'none': True, 'auto': True, 'required': True}}
    result.update(changes)
    return result


def completion(value=None, *, calls=None, reason='stop', reasoning=None):
    message = {'role': 'assistant', 'content': json.dumps(value) if value is not None else None}
    if calls is not None:message['tool_calls'] = calls
    if reasoning is not None:message['reasoning_details'] = deepcopy(reasoning)
    return {'id': 'generation-test', 'model': 'nvidia/nemotron-3-ultra-550b-a55b:free',
            'choices': [{'message': message, 'finish_reason': reason}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 25, 'total_tokens': 125,
                      'completion_tokens_details': {'reasoning_tokens': 10}, 'cost': 0}}


def call(identity, name, args):
    return {'id': identity, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


def tools(amount, prefix='initial', *, finish=True, reasoning=None):
    calls = [call(prefix+'-calc', 'calculate_strategy', {'plans': [{'goal_ref': 'goal-1', 'contribution': amount}]})]
    if finish:calls.append(call(prefix+'-finish', 'finish_planning', {}))
    return completion(calls=calls, reason='tool_calls', reasoning=reasoning)


class OpenRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = demo_contexts()['normal']

    def setUp(self):
        self.env = patch.dict(os.environ, {'OPENROUTER_API_KEY': 'offline-openrouter-key',
            'OPENROUTER_MODEL': 'nvidia/nemotron-3-ultra-550b-a55b:free',
            'OPENROUTER_BASE_URL': 'https://openrouter.ai/api/v1', 'OPENROUTER_PROFILE': 'tools_json_text',
            'OPENROUTER_ALLOW_PAID': 'false', 'OPENROUTER_REASONING_EFFORT': 'low',
            'PLANNER_PROVIDER': 'gemini'})
        self.env.start(); self.addCleanup(self.env.stop)

    def transport(self, *responses, metadata=None, clock=None, latency=0):
        self.requests = []; self.starts = []; remaining = list(responses)
        def handler(request):
            if request.method == 'GET':
                self.assertNotIn('authorization', request.headers)
                return httpx.Response(200, json={'data': {'endpoints': [metadata or endpoint()]}})
            self.requests.append(json.loads(request.content));self.starts.append(clock.now if clock else 0)
            if clock:clock.now += latency
            value = remaining.pop(0)
            if isinstance(value, Exception):raise value
            return value if isinstance(value, httpx.Response) else httpx.Response(200, json=value)
        client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return OpenRouterTransport(env_file=None, client=client)

    def agent(self, transport, *, clock=None, **kw):
        clock = clock or Clock()
        return GeminiPlanner(transport, clock=clock, sleeper=clock.sleep, jitter=lambda low, high: high,
            retry_policy=kw.pop('retry_policy', GenerationRetryPolicy(max_retries=0)), **kw)

    def ask(self, agent, **kw):
        ctx = self.context
        return agent.run(ctx, goal_ref='goal-1', question='Assess this synthetic goal',
            safety_checker=lambda draft: assess_draft(draft, ctx, draft['baseline_calculations'], expected_goal_ref='goal-1'), **kw)

    def test_factory_default_and_explicit_provider_and_paid_guards(self):
        self.assertEqual(configured_transport(None).provider, 'google_genai')
        with patch.dict(os.environ, {'PLANNER_PROVIDER': 'openrouter'}):
            transport = configured_transport(None);self.addCleanup(transport.client.close)
            self.assertEqual(transport.provider, 'openrouter')
        for change in [{'PLANNER_PROVIDER': 'unknown'}, {'OPENROUTER_MODEL': 'vendor/paid'},
                       {'OPENROUTER_MODEL': 'openrouter/free'}, {'OPENROUTER_PROFILE': 'unsupported'},
                       {'OPENROUTER_BASE_URL': 'https://untrusted.example/api/v1'}]:
            with patch.dict(os.environ, change), self.assertRaises(PlannerError):
                configured_transport(None) if 'PLANNER_PROVIDER' in change else OpenRouterTransport(None)

    def test_complete_loop_multiple_calls_matched_ids_and_reasoning(self):
        reasoning = [{'type': 'reasoning.encrypted', 'data': 'opaque', 'signature': 'unaltered'}]
        t = self.transport(tools(600, reasoning=reasoning), completion(output()))
        draft = self.ask(self.agent(t))
        self.assertEqual(draft['safety_assessment']['status'], 'PASS')
        self.assertFalse(draft['activation_allowed']);self.assertTrue(draft['model_generated'])
        self.assertEqual(draft['metadata']['provider'], 'openrouter')
        self.assertEqual(draft['metadata']['reasoning_tokens'], 20)
        self.assertEqual(draft['metadata']['reported_cost'], 0)
        messages = self.requests[1]['messages']
        self.assertEqual(next(m for m in messages if m['role'] == 'assistant')['reasoning_details'], reasoning)
        self.assertEqual([m['tool_call_id'] for m in messages if m['role'] == 'tool'], ['initial-calc', 'initial-finish'])
        self.assertEqual(self.requests[0]['tool_choice'], 'required')
        self.assertEqual(self.requests[1]['tool_choice'], 'none')
        self.assertNotIn('response_format', self.requests[1])
        self.assertFalse(self.requests[0]['provider']['allow_fallbacks'])

    def test_sequential_tool_rounds_and_pacing_all_phases(self):
        clock = Clock()
        t = self.transport(tools(1100, finish=False), tools(1200, 'second'), completion(output()),
                           tools(600, 'revision'), completion(output(('calculation-3',))), clock=clock)
        draft = self.ask(self.agent(t, clock=clock))
        self.assertEqual(draft['safety_assessment']['status'], 'PASS')
        self.assertEqual(draft['safety_revision']['revision_count'], 1)
        self.assertEqual(self.requests[1]['tool_choice'], 'auto')
        self.assertEqual(len(self.starts), 5)
        self.assertTrue(all(b-a >= 4.99 for a,b in zip(self.starts,self.starts[1:])))

    def test_slow_request_does_not_add_pacing_wait(self):
        clock = Clock();t = self.transport(tools(600), completion(output()), clock=clock, latency=6)
        self.ask(self.agent(t, clock=clock))
        self.assertEqual(clock.delays, [])

    def test_429_after_fresh_safety_calculation_resumes_exact_correction(self):
        invalid = output(('calculation-2',));invalid['strategies'][0]['rationale'] = 'Finishes exactly on the deadline.'
        t = self.transport(tools(1100), completion(output()), tools(600, 'revision'), completion(invalid),
            httpx.Response(429, headers={'Retry-After': '7'}, json={'error': {'code': 429, 'message': 'temporary rate limit'}}))
        a = self.agent(t)
        with patch.object(PlanningTools, 'calculate_strategy', autospec=True, side_effect=PlanningTools.calculate_strategy) as calculate:
            with self.assertRaises(PlannerError):self.ask(a)
            checkpoint = json.loads(json.dumps(a.last_checkpoint))
            state = unpack(checkpoint['state'])
            self.assertEqual(state['phase'], 'safety_correction')
            self.assertEqual(state['pending']['stage'], 'safety_json_correction')
            self.assertEqual(len(state['calculations']), 2)
            self.assertEqual(state['safety']['revision_count'], 1)
            t2 = self.transport(completion(output(('calculation-2',))))
            draft = self.ask(self.agent(t2), checkpoint=checkpoint)
        self.assertEqual(calculate.call_count, 2)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(draft['metadata']['correction_attempts'], 1)
        self.assertEqual(draft['safety_revision']['history'][0]['safety_assessment']['status'], 'REVISE')
        self.assertEqual(draft['safety_assessment']['status'], 'PASS')

    def test_provider_model_and_profile_checkpoint_pinning(self):
        t = self.transport(tools(600), httpx.Response(503, json={'error': {'code':503}}))
        a = self.agent(t)
        with self.assertRaises(PlannerError):self.ask(a)
        for field, value in [('provider','google_genai'), ('model','different'), ('profile','tools_structured')]:
            checkpoint = deepcopy(a.last_checkpoint);checkpoint['identity'][field] = value
            with self.assertRaises(PlannerError) as error:self.ask(self.agent(t), checkpoint=checkpoint)
            self.assertEqual(error.exception.code, 'planner_invalid_checkpoint')

    def test_partial_tool_batch_roundtrip_does_not_repeat_calculation(self):
        reasoning=[{'type':'reasoning.encrypted','data':'preserve-byte-for-byte'}]
        t=self.transport(tools(600,reasoning=reasoning));a=self.agent(t)
        def interrupt(saved):
            if unpack(saved['state'])['processed']==1:
                raise PlannerError('planner_timeout','simulated interruption',504,resume_allowed=True)
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calculate:
            with self.assertRaises(PlannerError):self.ask(a,on_checkpoint=interrupt)
            cp=json.loads(json.dumps(a.last_checkpoint))
            t2=self.transport(completion(output()))
            draft=self.ask(self.agent(t2),checkpoint=cp)
        self.assertEqual(calculate.call_count,1)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(draft['metadata']['total_tokens'],250)
        self.assertEqual(next(m for m in self.requests[0]['messages'] if m['role']=='assistant')['reasoning_details'],reasoning)

    def test_http_200_provider_errors_are_not_successful_responses(self):
        for payload in [{'error':{'code':503,'message':'PRIVATE'}},
                        {'choices':[{'finish_reason':'error','error':{'code':502,'message':'PRIVATE'}}]}]:
            t=self.transport(payload)
            with self.assertRaises(PlannerError) as error:self.ask(self.agent(t))
            self.assertEqual(error.exception.code,'planner_provider_error')
            self.assertTrue(error.exception.retryable)
            self.assertNotIn('PRIVATE',str(error.exception))

    def test_daily_quota_and_credentials_never_start_automatic_retries(self):
        for status,message in [(429,'daily quota exhausted'),(401,'invalid credentials')]:
            t=self.transport(httpx.Response(status,json={'error':{'code':status,'message':message}}))
            a=self.agent(t,retry_policy=GenerationRetryPolicy(max_retries=4))
            with self.assertRaises(PlannerError):self.ask(a)
            self.assertEqual(len(self.requests),1)
            self.assertEqual(a.last_metrics['retry_attempts'],0)

    def test_transient_retry_guidance_and_pacing_share_one_controller(self):
        clock=Clock();t=self.transport(httpx.Response(429, headers={'Retry-After':'7'},
            json={'error':{'code':429,'message':'Too many requests'}}), tools(600), completion(output()), clock=clock)
        draft=self.ask(self.agent(t,clock=clock,retry_policy=GenerationRetryPolicy(1,20,.1,.1)))
        self.assertEqual(draft['metadata']['retry_attempts'],1)
        self.assertGreaterEqual(self.starts[1]-self.starts[0],7)
        self.assertGreaterEqual(self.starts[2]-self.starts[1],4.99)

    def test_error_classification_permanent_and_transient(self):
        config=types.GenerateContentConfig(system_instruction='test',max_output_tokens=100,
            tool_config=types.ToolConfig(function_calling_config=types.FunctionCallingConfig(mode='NONE')))
        for status, message, headers, retryable in [(429,'daily quota exhausted',{'Retry-After':'2'},False),
            (429,'Too many requests per minute',{},True),(429,'unknown',{},False),
            (401,'invalid credentials',{},False),(402,'insufficient credits',{},False),
            (400,'invalid schema',{},False),(503,'temporarily unavailable',{},True)]:
            with self.subTest(status=status,message=message):
                t=self.transport(httpx.Response(status,headers=headers,json={'error':{'code':status,'message':message+' PRIVATE'}}))
                with self.assertRaises(PlannerError) as error:t.generate([],config,30000)
                self.assertEqual(error.exception.retryable,retryable)
                self.assertNotIn('PRIVATE',json.dumps(error.exception.provider_details))

    def test_no_header_temporary_429_uses_bounded_backoff(self):
        clock=Clock();t=self.transport(httpx.Response(429,json={'error':{'code':429,'message':'temporary rate limit'}}),
            tools(600),completion(output()),clock=clock)
        d=self.ask(self.agent(t,clock=clock,retry_policy=GenerationRetryPolicy(1,20,5,5)))
        self.assertEqual(d['metadata']['retry_attempts'],1)
        self.assertTrue(all(b-a>=4.99 for a,b in zip(self.starts,self.starts[1:])))

    def test_structured_capable_alternative_uses_same_orchestration(self):
        with patch.dict(os.environ,{'OPENROUTER_MODEL':'other/model:free','OPENROUTER_PROFILE':'tools_structured'}):
            metadata=endpoint(supported_parameters=endpoint()['supported_parameters']+['response_format','structured_outputs'])
            t=self.transport(tools(600),completion(output()),metadata=metadata)
            d=self.ask(self.agent(t))
        self.assertEqual(d['safety_assessment']['status'],'PASS')
        self.assertEqual(self.requests[0]['model'],'other/model:free')
        self.assertEqual(self.requests[1]['response_format']['type'],'json_schema')

    def test_pacing_deadline_preserves_pending_generation(self):
        t=self.transport(tools(600),completion(output()))
        a=self.agent(t,timeout_seconds=4)
        with self.assertRaises(PlannerError) as error:self.ask(a)
        self.assertEqual(error.exception.code,'planner_limit')
        self.assertEqual(len(self.requests),1)
        self.assertEqual(unpack(a.last_checkpoint['state'])['pending']['stage'],'json_final')

    def test_service_persists_and_resumes_safety_revision_without_goal_changes(self):
        from tests.test_safety_revision import SafetyRevisionPersistenceTests
        from planning.planner_service import PlannerService
        from planning.demo import AS_OF
        SafetyRevisionPersistenceTests.setUp(self)
        before=self.store.read(self.customer,AS_OF)
        invalid=output(('calculation-2',));invalid['strategies'][0]['rationale']='Finishes exactly on the deadline.'
        t=self.transport(tools(1100),completion(output()),tools(600,'revision'),completion(invalid),
            httpx.Response(429,headers={'Retry-After':'7'},json={'error':{'code':429,'message':'temporary rate limit'}}))
        service=PlannerService(self.store,t,cooldown_seconds=0)
        args={'goal_id':'goal-id','question':'Assess','request_id':'openrouter-recovery','expected_revision':1}
        with patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'0','PLANNER_MIN_REQUEST_INTERVAL_SECONDS':'0'}):
            with self.assertRaises(PlannerError):service.generate(self.bank,self.customer,**args)
            row=service.store.request(self.customer,args['request_id'])
            self.assertEqual(row['status'],'interrupted');self.assertNotIn('draft',row)
            self.assertEqual(row['provider'],'openrouter')
            self.assertEqual(row['safety_revision']['new_calculation_refs'],['calculation-2'])
            with self.assertRaises(PlannerError):service.retry(self.bank,self.customer,args['request_id'],expected_revision=1)
            # Simulate the provider's explicit wait elapsing; no financial inputs change.
            service.store.alter(self.customer,args['request_id'],lambda r:{**r,'retry_not_before':0})
            t2=self.transport(completion(output(('calculation-2',))))
            restarted=PlannerService(self.store,t2,cooldown_seconds=0)
            restarted.retry(self.bank,self.customer,args['request_id'],expected_revision=1)
            restarted.jobs[(self.customer,args['request_id'])].join(10)
            status=restarted.request_status(self.bank,self.customer,args['request_id'])
        self.assertEqual(status['status'],'completed',status)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(status['draft']['safety_assessment']['status'],'PASS')
        self.assertEqual(status['draft']['safety_revision']['revision_count'],1)
        self.assertEqual(status['draft']['metadata']['correction_attempts'],1)
        self.assertFalse(status['draft']['activation_allowed'])
        self.assertEqual(self.store.read(self.customer,AS_OF),before)

    def test_invalid_arguments_and_truncated_responses_fail_closed(self):
        bad=tools(600);bad['choices'][0]['message']['tool_calls'][0]['function']['arguments']='{"plans":[],"plans":[]}'
        duplicate=tools(600);duplicate['choices'][0]['message']['tool_calls'][1]['id']='initial-calc'
        for response in [bad,duplicate,completion(output(),reason='length'),completion(),
                         completion(calls=[call('bad','calculate_strategy',[])],reason='tool_calls')]:
            with self.subTest(response=response):
                with self.assertRaises(PlannerError):self.ask(self.agent(self.transport(response)))
        for raw in ['{"x":NaN}', '{"x":1e999}', '{"x":1,"x":2}']:
            with self.assertRaises(ValueError):strict_json(raw)

    def test_unsupported_endpoint_and_structured_profile_rejected_before_inference(self):
        for metadata in [endpoint(supported_parameters=['max_tokens']),
                         endpoint(supports_tool_choice={'none':True,'auto':True,'required':False}),
                         endpoint(pricing={'prompt':'1','completion':'0'})]:
            t=self.transport(metadata=metadata)
            with self.assertRaises(PlannerError):self.ask(self.agent(t))
            self.assertEqual(self.requests,[])
        with patch.dict(os.environ,{'OPENROUTER_PROFILE':'tools_structured'}):
            t=self.transport()
            with self.assertRaises(PlannerError):self.ask(self.agent(t))
            self.assertEqual(self.requests,[])

    def test_context_limit_and_cancellation_during_pacing(self):
        t=self.transport(metadata=endpoint(context_length=100))
        with self.assertRaises(PlannerError):self.ask(self.agent(t))
        self.assertEqual(self.requests,[])
        cancelled=[False];clock=Clock();t=self.transport(tools(600),completion(output()),clock=clock)
        def sleep(delay):clock.sleep(delay);cancelled[0]=True
        a=self.agent(t,clock=clock);a.sleeper=sleep
        with self.assertRaises(PlannerError) as error:self.ask(a,cancelled=lambda:cancelled[0])
        self.assertEqual(error.exception.code,'planner_cancelled');self.assertEqual(len(self.requests),1)

    def test_credentials_redacted_and_legacy_gemini_checkpoint_accepted(self):
        self.assertNotIn('offline-openrouter-key', private_text('offline-openrouter-key'))
        t=MockGemini(error=PlannerError('planner_timeout','wait',504,resume_allowed=True))
        a=self.agent(t)
        with self.assertRaises(PlannerError):self.ask(a)
        cp=deepcopy(a.last_checkpoint);cp['version']=1;cp.pop('identity')
        draft=self.ask(self.agent(MockGemini(alternatives=False)),checkpoint=cp)
        self.assertFalse(draft['activation_allowed'])


if __name__ == '__main__':unittest.main()
