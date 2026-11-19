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
from planning.openrouter import OpenRouterTransport, OpenRouterResponse, strict_json
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


class FencedJSONTests(unittest.TestCase):
    def response(self, text):
        payload = completion()
        payload['choices'][0]['message']['content'] = text
        return OpenRouterResponse(payload, 'test-route')

    def test_single_fence_preserves_values_raw_evidence_and_restore(self):
        body = '{"id":"calculation-1","amount":50.00,"unknown":null,"enabled":false}'
        for fence in ('```json\n'+body+'\n```', ' \n```\r\n'+body+'\r\n```\n '):
            with self.subTest(fence=fence):
                response = self.response(fence)
                self.assertEqual(response.text, body)
                self.assertEqual(response.raw_text, fence)
                saved = response.model_dump()
                self.assertEqual(saved['formatting_normalization'], 'single_json_markdown_fence')
                self.assertEqual(saved['openrouter_response']['choices'][0]['message']['content'], fence)
                restored = OpenRouterResponse(saved['openrouter_response'], saved['route'])
                self.assertEqual(restored.text, body)
                self.assertEqual(restored.model_dump(), saved)
        raw = self.response(body)
        self.assertEqual(raw.text, body)
        self.assertIsNone(raw.formatting_normalization)

    def test_invalid_wrappers_and_unsupported_json_are_not_normalized(self):
        cases = ['```json\n{}', '{}\n```', 'Here:\n```json\n{}\n```',
                 '```json\n{}\n```\nDone', '```json\n{}\n```\n```json\n{}\n```',
                 '```python\n{}\n```', '```json\n{broken}\n```',
                 '```json\n{"x":NaN}\n```', '```json\n{"x":Infinity}\n```',
                 '```json\n{"x":1e999}\n```', '```json\n{"x":1,"x":2}\n```']
        for text in cases:
            with self.subTest(text=text):
                response = self.response(text)
                self.assertEqual(response.text, text)
                self.assertIsNone(response.formatting_normalization)
                with self.assertRaises(ValueError): strict_json(response.text)

    def test_fenced_schema_invalid_content_is_still_rejected(self):
        from planning.proposal_validation import validate_proposal, ProposalValidationError
        ctx = demo_contexts()['normal']
        calculation = PlanningTools(ctx).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
        response = self.response('```json\n{"unsupported":true}\n```')
        self.assertIsNotNone(response.formatting_normalization)
        with self.assertRaises(ProposalValidationError):
            validate_proposal(response.text, {calculation['calculation_ref']:calculation}, ctx)


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

    def test_deepseek_defaults_and_paid_model_allowlist(self):
        with patch.dict(os.environ, {'OPENROUTER_API_KEY':'offline-key', 'OPENROUTER_ALLOW_PAID':'true'}, clear=True):
            transport=configured_transport(None);self.addCleanup(transport.client.close)
            self.assertEqual(transport.provider,'openrouter')
            self.assertEqual(transport.model,'deepseek/deepseek-v3.2')
            self.assertEqual(transport.profile,'tools_json_text')
            self.assertTrue(transport.allow_paid)
            for model in ('vendor/paid','deepseek/deepseek-v3.2-special','openrouter/auto'):
                with patch.dict(os.environ,{'OPENROUTER_MODEL':model}),self.assertRaises(PlannerError):
                    OpenRouterTransport(None)
            with patch.dict(os.environ,{'OPENROUTER_ALLOW_PAID':'false'}),self.assertRaises(PlannerError):
                OpenRouterTransport(None)
            with patch.dict(os.environ,{'OPENROUTER_MODEL':'nvidia/nemotron-3-ultra-550b-a55b:free'}):
                free=OpenRouterTransport(None);self.addCleanup(free.client.close)
                self.assertFalse(free.allow_paid)
            with patch.dict(os.environ,{'PLANNER_PROVIDER':'gemini'}):
                self.assertEqual(configured_transport(None).provider,'google_genai')

    def test_fenced_final_and_ready_checkpoint_replay_do_not_spend_corrections(self):
        payload = completion(output())
        original = '```json\n'+payload['choices'][0]['message']['content']+'\n```'
        payload['choices'][0]['message']['content'] = original
        transport = self.transport(tools(600), payload)
        saved = []
        draft = self.ask(self.agent(transport), on_checkpoint=lambda cp:saved.append(deepcopy(cp)))
        self.assertEqual(draft['safety_assessment']['status'], 'PASS')
        checkpoint = saved[-1]
        state = unpack(checkpoint['state'])
        self.assertEqual(state['ready']['formatting_normalization'], 'single_json_markdown_fence')
        self.assertEqual(state['ready']['openrouter_response']['choices'][0]['message']['content'], original)
        state['metrics']['correction_attempts'] = 4
        checkpoint['state'] = pack(state)
        before = deepcopy(checkpoint)
        with patch.object(transport, 'generate', side_effect=AssertionError('No new request')):
            recovered = self.ask(self.agent(transport), checkpoint=checkpoint)
        self.assertEqual(recovered['safety_assessment']['status'], 'PASS')
        self.assertEqual(recovered['metadata']['correction_attempts'], 4)
        self.assertEqual(recovered['metadata']['model_calls'], draft['metadata']['model_calls'])
        self.assertEqual(recovered['baseline_calculations'], draft['baseline_calculations'])
        self.assertEqual(checkpoint, before)

    def test_complete_loop_multiple_calls_matched_ids_and_reasoning(self):
        reasoning = [{'type': 'reasoning.encrypted', 'data': 'opaque', 'signature': 'unaltered'}]
        t = self.transport(tools(600, reasoning=reasoning), completion(output()))
        a=self.agent(t);draft = self.ask(a)
        self.assertEqual(draft['safety_assessment']['status'], 'PASS')
        self.assertFalse(draft['activation_allowed']);self.assertTrue(draft['model_generated'])
        self.assertEqual(draft['metadata']['provider'], 'openrouter')
        self.assertEqual(draft['metadata']['reasoning_tokens'], 20)
        self.assertEqual(draft['metadata']['reported_cost'], 0)
        messages = self.requests[1]['messages']
        self.assertEqual([m['role'] for m in messages],['system','user'])
        saved=unpack(a.last_checkpoint['state'])['contents']
        self.assertEqual(next(c['openrouter_message'] for c in saved if 'openrouter_message'in c)['reasoning_details'],reasoning)
        self.assertEqual([p['function_response']['id'] for c in saved for p in c.get('parts',[]) if 'function_response'in p],['initial-calc','initial-finish'])
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
            recovered=self.agent(t2);draft=self.ask(recovered,checkpoint=cp)
        self.assertEqual(calculate.call_count,1)
        self.assertEqual(len(self.requests),1)
        self.assertEqual(draft['metadata']['total_tokens'],250)
        self.assertEqual([m['role'] for m in self.requests[0]['messages']],['system','user'])
        saved=unpack(recovered.last_checkpoint['state'])['contents']
        self.assertEqual(next(c['openrouter_message'] for c in saved if 'openrouter_message'in c)['reasoning_details'],reasoning)

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


class WireCompactionTests(unittest.TestCase):
    @staticmethod
    def expand(value):
        shared=value.get('_shared_values',{})
        def merge(a,b):
            out=deepcopy(a)
            for k,v in b.items():
                out[k]=merge(out[k],v) if isinstance(out.get(k),dict) and isinstance(v,dict) else deepcopy(v)
            return out
        def walk(v):
            if isinstance(v,list):return [walk(x) for x in v]
            if not isinstance(v,dict):return v
            if set(v)=={'_shared_text'}:return shared[v['_shared_text']]
            if set(v)=={'_table_fields','_table_rows'}:return [walk(dict(zip(v['_table_fields'],r))) for r in v['_table_rows']]
            fields={k:walk(x) for k,x in v.items() if k not in {'_shared_fields','_shared_values','_wire_format'}}
            return merge(shared[v['_shared_fields']],fields) if '_shared_fields' in v else fields
        return walk(value)

    def test_nested_common_financial_values_roundtrip_without_mutating_ledger(self):
        from planning.openrouter import compact_json
        common={'assumptions':['Estimated spending; not guaranteed.']*20,'period':{'as_of':'2026-11-15','basis':'observed'},'reserve':1000.0}
        original={'calculation_ref':'calculation-1','rows':[{**deepcopy(common),'cash':i,'nested':{'same':common,'different':i}} for i in range(8)]}
        before=deepcopy(original);encoded=compact_json(original)
        self.assertLess(len(encoded),len(json.dumps(original))*.7)
        self.assertEqual(json.dumps(self.expand(json.loads(encoded)),sort_keys=True),json.dumps(original,sort_keys=True))
        self.assertEqual(original,before)

    def test_booleans_and_numbers_are_not_shared_as_equal(self):
        from planning.openrouter import compact_json
        original={'rows':[{'flag':v,'long':'x'*1200} for v in (True,1,1.0,False)]}
        self.assertEqual(json.dumps(self.expand(json.loads(compact_json(original))),sort_keys=True),json.dumps(original,sort_keys=True))

    def test_reserved_input_keys_cannot_impersonate_wire_references(self):
        from planning.openrouter import compact_json
        original={'rows':[{'_shared_fields':'untrusted','note':'x'*1000}]*5}
        self.assertEqual(json.loads(compact_json(original)),original)

    def test_native_reasoning_and_tool_ids_remain_exact(self):
        from planning.openrouter import NativeMessage
        native=NativeMessage({'role':'assistant','content':'unchanged','reasoning_details':[{'opaque':'signature'}],
            'tool_calls':[call('same-id','calculate_strategy',{})]},'nvidia')
        before=deepcopy(native.message)
        t=object.__new__(OpenRouterTransport)
        result={'calculation_ref':'calculation-1','rows':[{'assumptions':'x'*1500,'cash':n} for n in range(5)]}
        messages,route=t._messages([native,types.Content(role='user',parts=[types.Part(function_response=types.FunctionResponse(name='calculate_strategy',id='same-id',response=result))])])
        self.assertEqual(messages[0],before);self.assertEqual(native.message,before)
        self.assertEqual(messages[1]['tool_call_id'],'same-id')
        self.assertEqual(self.expand(json.loads(messages[1]['content'])),result)


class CorrectionWireTests(unittest.TestCase):
    def test_feedback_keeps_scoped_errors_without_repeating_draft_or_ledger(self):
        from planning.openrouter import correction_feedback,compact_json
        original={'task':'Correct the complete proposal using the validation evidence below.',
            'original_proposal':'x'*50000,'authoritative_python_calculations':{'calculation-1':{'large':'x'*50000}},
            'authoritative_financial_facts':{'large':'x'*50000},'authoritative_date_facts':{'large':'x'*50000},
            'validation_errors':[{'field':'answer','error_type':'monetary_claim_mismatch','original_claim':'x'*50000,
                'calculation_ref':'calculation-1','authoritative_values':{'cash':3172.81},'correction_required':'Quote the computed dated minimum.'}]}
        before=deepcopy(original);lean=correction_feedback(original)
        self.assertLess(len(compact_json(lean)),2000)
        self.assertEqual(lean['authoritative_calculation_refs'],['calculation-1'])
        self.assertEqual(lean['validation_errors'][0]['authoritative_values'],{'cash':3172.81})
        self.assertNotIn('original_proposal',lean);self.assertNotIn('original_claim',lean['validation_errors'][0])
        self.assertEqual(original,before)

    def test_earlier_feedback_is_superseded_without_changing_native_output(self):
        from planning.openrouter import NativeMessage
        t=object.__new__(OpenRouterTransport)
        feedback={'task':'Correct the complete proposal using the validation evidence below.','validation_errors':[],
            'original_proposal':'rejected','authoritative_python_calculations':{'calculation-1':{}}}
        native=NativeMessage({'role':'assistant','content':'```json\n{}\n```','reasoning_details':[{'opaque':'preserved'}]},'siliconflow/fp8')
        contents=[]
        for _ in range(2):contents += [native,types.Content(role='user',parts=[types.Part.from_text(text=json.dumps(feedback))])]
        messages,_=t._messages(contents)
        self.assertEqual(messages[0],native.message);self.assertEqual(messages[2],native.message)
        self.assertIn('superseded',messages[1]['content'])
        self.assertEqual(json.loads(messages[3]['content'])['authoritative_calculation_refs'],['calculation-1'])

    def test_only_superseded_unsigned_drafts_are_compacted(self):
        from planning.openrouter import NativeMessage
        t=object.__new__(OpenRouterTransport)
        feedback={'task':'Correct the complete proposal using the validation evidence below.','validation_errors':[]}
        old=NativeMessage({'role':'assistant','content':'old rejected draft','reasoning':None},'siliconflow/fp8')
        latest=NativeMessage({'role':'assistant','content':'latest complete draft','reasoning':None},'siliconflow/fp8')
        user=types.Content(role='user',parts=[types.Part.from_text(text=json.dumps(feedback))])
        messages,_=t._messages([old,user,latest,user])
        self.assertIn('superseded',messages[0]['content'])
        self.assertEqual(messages[2]['content'],'latest complete draft')
        self.assertEqual(old.message['content'],'old rejected draft')

    def test_terminal_rejected_output_retains_precise_validation_records(self):
        from tests.test_gemini_corrections import Scripted
        from planning.proposal_validation import ProposalValidationError
        raw='```json\n'+json.dumps(output())+'\n```'
        agent=GeminiPlanner(Scripted([raw],amount=600),max_corrections=0)
        with self.assertRaises(ProposalValidationError):agent.run(demo_contexts()['normal'],goal_ref='goal-1',question='Assess')
        self.assertEqual(agent.last_validation_records[-1]['original_proposal'],raw)
        self.assertEqual(agent.last_validation_records[-1]['validation_errors'][0]['error_type'],'response_format')
        self.assertIn('calculation-1',agent.last_validation_records[-1]['authoritative_python_calculations'])


class CalculationWireReuseTests(unittest.TestCase):
    def test_unchanged_tool_result_references_prior_complete_evidence(self):
        from planning.openrouter import tool_wire
        first={'calculation_ref':'calculation-1','plans':[{'contribution':50}], 'assumptions':['Estimated spending']*30}
        before=deepcopy(first);prior={};self.assertEqual(tool_wire(first,prior),first)
        reused=tool_wire(deepcopy(first),prior)
        self.assertEqual(reused['calculation_ref'],'calculation-1')
        self.assertIn('unchanged_result_source',reused);self.assertEqual(first,before)

    def test_changed_financial_outputs_and_inputs_are_not_borrowed(self):
        from planning.openrouter import tool_wire
        first={'calculation_ref':'calculation-1','plans':[{'contribution':50}], 'assumptions':['Estimated spending']*30,
            'dated_cashflow':{'minimum_cash':3172.81,'timeline':[{'date':'2026-11-29','cash':3172.81}]}}
        prior={};tool_wire(first,prior);second=deepcopy(first);second['calculation_ref']='calculation-2';second['plans'][0]['contribution']=0;second['dated_cashflow']['minimum_cash']=3222.81;second['dated_cashflow']['timeline'][0]['cash']=3222.81
        value=tool_wire(second,prior)
        self.assertEqual(value['plans'],second['plans']);self.assertEqual(value['dated_cashflow']['minimum_cash'],3222.81)
        self.assertEqual(prior['calculation-1'],first);self.assertEqual(prior['calculation-2'],second)

    def test_final_facts_are_referenced_only_when_old_values_are_preserved(self):
        from planning.openrouter import dedupe_final_facts
        def message(facts,dates):return {'role':'user','content':'Now produce the final structured draft. Same instructions. Authoritative Python date facts (completion_date and deadline are distinct): '+json.dumps(dates)+' authoritative_financial_facts (source-labelled values, periods and assumptions; never interchange flow and cash stock): '+json.dumps(facts)}
        old={'version':1,'context':[{'cash':100}],'calculations':{'calculation-1':{'cash':100,'assumptions':['estimated']}}}
        new=deepcopy(old);new['calculations']['calculation-2']={'cash':90}
        messages=[message(old,{'calculation-1':[]}),message(new,{'calculation-1':[],'calculation-2':[]})]
        dedupe_final_facts(messages);self.assertIn('supplied once',messages[0]['content'])
        new['calculations']['calculation-1']['cash']=99
        messages=[message(old,{'calculation-1':[]}),message(new,{'calculation-1':[],'calculation-2':[]})];original=deepcopy(messages)
        dedupe_final_facts(messages);self.assertEqual(messages,original)


class CompactFinalStageTests(unittest.TestCase):
    def test_fresh_payload_reuses_authoritative_values_and_bounded_errors(self):
        from planning.openrouter import NativeMessage,expand_wire
        context=demo_contexts()['normal'];context['goal_versions']={'goal-1':1}
        tools=PlanningTools(context);calc=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600.0}]})
        native=NativeMessage({'role':'assistant','content':None,'reasoning_details':[{'opaque':'preserved'}],
            'tool_calls':[call('original-id','calculate_strategy',{})]},'nvidia')
        history=[native,types.Content(role='user',parts=[types.Part(function_response=types.FunctionResponse(name='calculate_strategy',id='original-id',response=calc))])]
        before=deepcopy((context,tools.calculations,native.message));t=object.__new__(OpenRouterTransport)
        errors=[{'field':'answer','error_type':'financial_narrative_mismatch','original_claim':'rejected '*10000,'authoritative_values':{'cash':123},'correction_required':'Use the exact source.'}]
        fresh=t.final_contents(context,tools.calculations,'goal-1','Assess',history,errors)
        self.assertEqual(len(fresh),1);self.assertEqual(fresh[0].role,'user')
        payload=expand_wire(json.loads(fresh[0].parts[0].text))
        self.assertEqual(payload['authoritative_calculations'],tools.calculations)
        self.assertEqual(payload['financial_context']['goal_versions'],{'goal-1':1})
        self.assertNotIn('original_claim',payload['validation_errors'][0])
        self.assertNotIn('reasoning_details',fresh[0].parts[0].text)
        self.assertEqual((context,tools.calculations,native.message),before)
        with self.assertRaises(ValueError):t.final_contents(context,tools.calculations,'goal-1','Assess',[native])
        with self.assertRaises(PlannerError):t.final_contents(context,{'wrong-ref':calc},'goal-1','Assess',history)

    def test_fresh_reasoning_is_archived_but_not_transplanted_into_tool_history(self):
        from planning.openrouter import NativeMessage
        native=NativeMessage({'role':'assistant','content':'Final answer','reasoning_details':[{'opaque':'new-conversation-signature'}]},'nvidia',True)
        t=object.__new__(OpenRouterTransport);saved=native.model_dump();restored=t.restore_content(saved)
        messages,_=t._messages([restored])
        self.assertNotIn('reasoning_details',messages[0]);self.assertEqual(saved['openrouter_message']['reasoning_details'],[{'opaque':'new-conversation-signature'}])
        self.assertTrue(restored.isolated_final)
