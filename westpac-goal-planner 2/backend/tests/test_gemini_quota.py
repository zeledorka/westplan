"""Offline quota tests. Scripted output is never evidence of live Gemini success."""
import json,os,unittest
from datetime import datetime
from unittest.mock import patch,MagicMock
import httpx
from google.genai import errors,types
from planning.quota import quota_guidance
from planning.gemini import GeminiTransport,PlannerError
from planning.agent_tools import PlanningTools
from planning.checkpoint import unpack
from tests import test_safety_revision as safety_tests
Transport,tool,plan=safety_tests.Transport,safety_tests.tool,safety_tests.plan
from tests.test_gemini_planner import output


def body(quota='GenerateRequestsPerMinutePerProject',value='10',delay='2s'):
    return {'error':{'status':'RESOURCE_EXHAUSTED','message':'PRIVATE never persist this message', 'details':[
        {'@type':'type.googleapis.com/google.rpc.QuotaFailure','violations':[{
            'quotaMetric':'generativelanguage.googleapis.com/generate_content_free_tier_requests',
            'quotaId':quota,'quotaValue':value,'subject':'projects/PRIVATE','description':'PRIVATE'}]},
        {'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':delay},
        {'@type':'type.googleapis.com/google.rpc.ErrorInfo','reason':'RATE_LIMIT_EXCEEDED','domain':'googleapis.com','metadata':{'consumer':'projects/PRIVATE'}}]}}


def failure(payload=None,headers=None):
    with patch.dict(os.environ,{'GEMINI_API_KEY':'offline-test'}):
        t=GeminiTransport(env_file=None);t.client=MagicMock()
        t.client.models.generate_content.side_effect=errors.APIError(429,payload or body(),response=httpx.Response(429,headers=headers))
        try:t.generate([],types.GenerateContentConfig(),1000)
        except PlannerError as exc:return exc
    raise AssertionError('Expected quota error')


class QuotaTests(unittest.TestCase):
    def test_retryinfo_and_header_are_honoured_and_private_fields_omitted(self):
        exc=failure(body(delay='75.5s'),{'Retry-After':'70'})
        self.assertTrue(exc.retryable);self.assertTrue(exc.resume_allowed)
        self.assertEqual(exc.retry_after_seconds,75.5)
        self.assertEqual(exc.provider_details['classification'],'temporary_rate_limit')
        self.assertNotIn('PRIVATE',json.dumps(exc.provider_details))
        self.assertTrue(exc.provider_details['retry_after_present'])

    def test_body_only_minute_and_unknown_guidance(self):
        self.assertEqual(failure(body(delay='bad')).retry_after_seconds,60)
        exc=failure({'error':{'details':[{'@type':'type.googleapis.com/google.rpc.RetryInfo','retryDelay':{'seconds':'8','nanos':500000000}}]}})
        self.assertEqual(exc.retry_after_seconds,8.5);self.assertTrue(exc.retryable)
        exc=failure({'error':{}},{'Retry-After':'invalid'})
        self.assertFalse(exc.retryable);self.assertTrue(exc.resume_allowed)
        self.assertEqual(exc.provider_details['classification'],'unknown')
        self.assertTrue(exc.provider_details['retry_after_present']);self.assertFalse(exc.provider_details['retry_after_valid'])

    def test_daily_reset_uses_pacific_midnight_and_zero_has_no_guessed_reset(self):
        for stamp,expected in [('2026-07-01T06:59:00+00:00',60),('2026-12-01T07:59:00+00:00',60)]:
            diagnostic,retry,delay=quota_guidance(body('GenerateRequestsPerDayPerProject',delay='2s'),now=datetime.fromisoformat(stamp))
            self.assertFalse(retry);self.assertEqual(delay,expected)
            self.assertEqual(diagnostic['classification'],'daily_quota')
        exc=failure(body(value='0'))
        self.assertFalse(exc.retryable);self.assertTrue(exc.resume_allowed)
        self.assertNotIn('expected_reset_at',exc.provider_details)
        self.assertEqual(exc.provider_details['classification'],'zero_quota')
        exc=failure(body('GenerateRequestsPerDayPerProject'))
        self.assertFalse(exc.retryable);self.assertTrue(exc.resume_allowed)

    def test_mixed_daily_limits_restrictions_and_malformed_metadata_are_conservative(self):
        payload=body('GenerateTokensPerDayPerProject')
        payload['error']['details'][0]['violations'][0]['quotaMetric']='generate_tokens'
        payload['error']['details'][0]['violations'].append({'quotaId':'GenerateRequestsPerMinute'})
        diagnostic,retry,_=quota_guidance(payload)
        self.assertFalse(retry);self.assertNotIn('expected_reset_at',diagnostic)
        payload=body();payload['error']['details'][2]['reason']='SERVICE_DISABLED'
        self.assertEqual(failure(payload).provider_details['classification'],'project_restriction')
        self.assertFalse(failure(payload).retryable)
        self.assertFalse(failure({'error':{'details':'invalid'}}).retryable)
        payload=body('UnknownQuota')
        payload['error']['details'][2]['reason']='DAILY_LIMIT_EXCEEDED'
        self.assertFalse(failure(payload).retryable)
        exc=failure(body(delay='172800s'))
        self.assertEqual(exc.retry_after_seconds,172800)


class QuotaSafetyTests(unittest.TestCase):
    setUpClass=classmethod(safety_tests.SafetyRevisionTests.setUpClass.__func__)
    run_agent=safety_tests.SafetyRevisionTests.run_agent
    ask=safety_tests.SafetyRevisionTests.ask
    # Five completed calculations, a safety finding and a recoverable narrative error.
    def pending(self,error):
        invalid=output(('calculation-5',));invalid['strategies'][0]['rationale']='Achieves the goal exactly on the deadline.'
        return Transport(tool(plan(1100),plan(1200),plan(1300)),output(),
                         tool(plan(600),plan(600,deadline='2027-06-30')),invalid,error)

    def test_transient_429_retries_exact_narrative_with_five_calculations(self):
        t=self.pending(failure());t.responses.append(output(('calculation-5',)))
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calc:
            d,a,clock=self.ask(t)
        self.assertEqual(calc.call_count,5);self.assertEqual(clock.delays,[60])
        self.assertEqual(t.calls[-1][0],t.calls[-2][0])
        self.assertEqual(t.calls[-1][1],t.calls[-2][1])
        self.assertEqual(d['metadata']['retry_attempts'],1)
        self.assertEqual(d['metadata']['correction_attempts'],1)
        self.assertEqual(d['metadata']['safety_revision_attempts'],1)
        self.assertEqual(len(d['baseline_calculations']),5)
        self.assertEqual(d['safety_assessment']['status'],'PASS');self.assertFalse(d['activation_allowed'])

    def test_daily_and_zero_checkpoint_resume_without_replaying_completed_math(self):
        for payload in (body('GenerateRequestsPerDayPerProject'),body(value='0')):
            t=self.pending(failure(payload));a,c,clock=self.run_agent(t)
            with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calc:
                with self.assertRaises(PlannerError):a.run(self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c)
                state=unpack(a.last_checkpoint['state'])
                self.assertEqual(state['pending']['stage'],'safety_json_correction')
                self.assertEqual(len(state['calculations']),5)
                self.assertEqual(len(state['safety']['history']),1)
                self.assertEqual(clock.delays,[])
                next_t=Transport(output(('calculation-5',)));b,c2,_=self.run_agent(next_t)
                d=b.run(self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c2,checkpoint=a.last_checkpoint)
            self.assertEqual(calc.call_count,5);self.assertEqual(len(next_t.calls),1)
            self.assertEqual(t.calls[-1][0],next_t.calls[0][0])
            self.assertEqual(t.calls[-1][1],next_t.calls[0][1])
            self.assertEqual(d['metadata']['correction_attempts'],1)
            self.assertEqual(d['metadata']['safety_revision_attempts'],1)
            self.assertEqual(d['safety_assessment']['status'],'PASS');self.assertFalse(d['activation_allowed'])

    def test_wait_exceeding_deadline_retains_quota_diagnostics_and_pending_call(self):
        t=self.pending(failure(body(delay='600s')));a,c,clock=self.run_agent(t,timeout_seconds=100)
        with self.assertRaises(PlannerError) as caught:
            a.run(self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c)
        self.assertEqual(caught.exception.upstream_status,429)
        self.assertEqual(caught.exception.retry_after_seconds,600)
        self.assertEqual(caught.exception.provider_details['classification'],'temporary_rate_limit')
        self.assertEqual(clock.delays,[])
        state=unpack(a.last_checkpoint['state'])
        self.assertEqual(state['pending']['stage'],'safety_json_correction')
        self.assertEqual(len(state['calculations']),5)


class QuotaPersistenceTests(unittest.TestCase):
    setUp=safety_tests.SafetyRevisionPersistenceTests.setUp
    ask=safety_tests.SafetyRevisionPersistenceTests.ask
    def test_quota_exhaustion_is_durable_and_explicit_resume_obeys_wait_and_context(self):
        self.planner.transport=QuotaSafetyTests.pending(self,failure(body(value='0')))
        before=self.store.read(self.customer,self.raw['as_of'])
        with self.assertRaises(PlannerError):self.ask()
        row=self.planner.store.request(self.customer,'request-1')
        self.assertEqual(row['status'],'interrupted');self.assertIsNotNone(row['checkpoint'])
        self.assertEqual(row['error']['provider_details']['classification'],'zero_quota')
        with self.assertRaises(PlannerError):self.planner.retry(self.bank,self.customer,'request-1',expected_revision=1)
        self.planner.store.alter(self.customer,'request-1',lambda r:{**r,'retry_not_before':0})
        self.assertEqual(len(unpack(row['checkpoint']['state'])['calculations']),5)
        self.planner.transport=Transport(output(('calculation-5',)))
        with patch.object(PlanningTools,'calculate_strategy',side_effect=AssertionError('Must reuse calculations')):
            self.planner.retry(self.bank,self.customer,'request-1',expected_revision=1)
            self.planner.jobs[(self.customer,'request-1')].join(30)
        status=self.planner.request_status(self.bank,self.customer,'request-1')
        self.assertEqual(status['status'],'completed',status)
        self.assertFalse(status['draft']['activation_allowed'])
        self.assertEqual(self.store.read(self.customer,self.raw['as_of']),before)
