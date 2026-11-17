"""Deterministic tests use a labelled mock transport, never a live API key."""
from copy import deepcopy
from datetime import date
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, MagicMock
import httpx
from google.genai import types, errors
from fastapi.testclient import TestClient
from planning.agent import GeminiPlanner, private_text
from planning.agent_tools import PlanningTools
from planning.agent_models import PlannerResult, CalculationRequest
from planning.gemini import GeminiTransport, PlannerError
from planning.demo import demo_contexts, demo_transactions, AS_OF
from planning.context import build_planning_context
from planning.schemas import AccountSnapshot, Goal
from planning.store import DemoStore, StaleState
from planning.service import BankingService
from planning.planner_service import PlannerService
from evaluation.shock_scenarios import scenarios


def tool_response(plans, *, unknown=False, finish=True):
    parts=[types.Part.from_function_call(name='not_authorised' if unknown else 'calculate_strategy',args={'plans':plans})]
    if finish: parts.append(types.Part.from_function_call(name='finish_planning',args={}))
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=parts))],response_id='mock-tool-response')


def output(refs=('calculation-1',), **changes):
    obj={'objective':'Assess the goal','answer':'These are calculated hypothetical options, with no goal activation.',
        'assessment':'feasible','change_recommended':False,'recommended_ref':refs[0] if refs else None,
        'strategies':[{'calculation_ref':ref,'title':f'Composed option {i+1}','approach':'Consider the evidence and calculated schedule.',
            'proposed_actions':['Review this hypothetical contribution.'],'rationale':'Python supplies the numbers.',
            'benefits':['Retains protected funds'],'drawbacks':['Timing can change'],'assumptions':['Income may vary']} for i,ref in enumerate(refs)],
        'assumptions':['No guaranteed returns'],'missing_information':[],'shock_refs':[]}
    obj.update(changes)
    return obj


class MockGemini:
    available=True;model='mock-gemini';provider='mock'
    def __init__(self, result=None, alternatives=True, error=None, change=None):
        self.result=result;self.alternatives=alternatives;self.error=error;self.calls=[];self.change=change
    def generate(self, contents, config, timeout_ms):
        self.calls.append((deepcopy(contents),config,timeout_ms))
        if self.error: raise self.error
        if not config.tools:
            if self.change: self.change()
            value=self.result if self.result is not None else output(('calculation-1','calculation-2') if self.alternatives else ('calculation-1',))
            return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=[types.Part.from_text(text=value if isinstance(value,str) else json.dumps(value))]))],response_id='mock-final-response')
        payload=json.loads(contents[0].parts[0].text)
        goals=payload['financial_context']['goals']
        parts=[]
        for amount in ([600,450] if self.alternatives else [600]):
            parts.append(types.Part.from_function_call(name='calculate_strategy',args={'plans':[{'goal_ref':g['goal_ref'],'contribution':amount} for g in goals]}))
        parts.append(types.Part.from_function_call(name='finish_planning',args={}))
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=parts))],response_id='mock-tool-response')

class ArithmeticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.ctx=demo_contexts()
    def test_normal_paid_unpaid_context_and_no_double_counting(self):
        for name,capacity,balance in [('normal',900,2000),('vet_paid',490.73,150),('vet_unpaid',900,150)]:
            with self.subTest(name=name):
                before=deepcopy(self.ctx[name]);t=PlanningTools(before)
                c=t.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
                self.assertEqual(c['comfortable_capacity'],capacity)
                self.assertEqual(c['current_projected_cash_after_known_obligations'],balance)
                self.assertEqual(c['plans'][0]['required_per_period'],600)
                self.assertEqual(c['plans'][0]['completion_date'],'2027-05-30')
                self.assertEqual(before,self.ctx[name])
    def test_arbitrary_strategies_fortnightly_pauses_targets_deadlines(self):
        tools=PlanningTools(self.ctx['normal'])
        c=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':137.29,'frequency':'fortnightly','pause_months':2,'target':5100,'deadline':'2028-06-15','priority':2}]})
        p=c['plans'][0]
        self.assertEqual(p['contribution'],137.29);self.assertEqual(p['priority'],2)
        self.assertEqual(p['monthly_equivalent'],297.46)
        self.assertIsNotNone(p['completion_date'])
    def test_money_precision_and_ambiguous_funding_are_explicit(self):
        ctx=deepcopy(self.ctx['normal'])
        ctx['financial_insights']['balances'].append({'kind':'transaction','balance':100,'protected':50})
        c=PlanningTools(ctx).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':137.293}]})
        self.assertEqual(c['plans'][0]['contribution'],137.29)
        self.assertFalse(c['funding_reserve_known'])
        self.assertIsNone(c['one_month_comparison'])

    def test_multiple_goals_compete_for_capacity(self):
        ctx=deepcopy(self.ctx['normal']);ctx['goals'].append({'goal_ref':'goal-2','kind':'emergency_fund','target':3000,'allocated':0,'deadline':'2027-05-31'})
        tools=PlanningTools(ctx)
        c=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600},{'goal_ref':'goal-2','contribution':400}]})
        self.assertEqual(c['aggregate_monthly_equivalent'],1000)
        self.assertFalse(c['within_comfortable_capacity'])
        with self.assertRaises(ValueError):tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
    def test_unachievable_deadline_zero_contribution_and_hypothetical_shocks(self):
        c=PlanningTools(self.ctx['normal']).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':0,'deadline':'2026-10-01'}],
            'hypothetical_missing_income':500,'hypothetical_one_off_expense':75})
        self.assertFalse(c['plans'][0]['meets_deadline']);self.assertIsNone(c['plans'][0]['required_per_period'])
        self.assertIsNone(c['plans'][0]['completion_date'])
        self.assertEqual(c['one_month_comparison']['liquid_after_pending_reserve_and_contribution'],1425)
    def test_foreign_goals_invalid_amounts_and_unsupported_actions_rejected(self):
        tools=PlanningTools(self.ctx['normal'])
        for plan in [{'goal_ref':'foreign','contribution':1},{'goal_ref':'goal-1','contribution':float('inf')},
            {'goal_ref':'goal-1','contribution':True},{'goal_ref':'goal-1','contribution':1,'transfer':100},
            {'goal_ref':'goal-1','contribution':1,'target':100}]:
            with self.subTest(plan=plan),self.assertRaises(ValueError): tools.calculate_strategy({'plans':[plan]})

class AgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.ctx=demo_contexts()['normal']
    def test_iterative_tool_results_are_sent_to_same_model_and_figures_are_server_owned(self):
        mock=MockGemini();d=GeminiPlanner(mock).run(self.ctx,goal_ref='goal-1',question='Give me another option')
        self.assertEqual(d['status'],'draft_unverified');self.assertFalse(d['activation_allowed'])
        self.assertFalse(d['model_generated']);self.assertEqual(d['metadata']['provider'],'mock')
        self.assertEqual(len(mock.calls),2);self.assertEqual(d['metadata']['tool_calls'],3)
        self.assertEqual(d['metadata']['tool_sequence'],['calculate_strategy','calculate_strategy','finish_planning'])
        self.assertEqual(d['metadata']['sdk_version'],'3.3.0')
        self.assertFalse(d['metadata']['usage_reported'])
        self.assertTrue(any(p.function_response for content in mock.calls[-1][0] for p in content.parts))
        self.assertEqual(d['calculations']['calculation-1']['plans'][0]['contribution'],600)
    def test_invalid_json_structure_references_and_claimed_actions_fail(self):
        for result in ['not json',output(('invented',)),output(shock_refs=['foreign']),{**output(),'activate':True},
            output(recommended_ref='uncomputed'),output(change_recommended='true')]:
            with self.subTest(result=result),self.assertRaises(PlannerError):
                GeminiPlanner(MockGemini(result=result)).run(self.ctx,goal_ref='goal-1',question='Assess')
    def test_insufficient_information_no_change_and_no_forced_alternatives(self):
        for result in [output(change_recommended=False),output((),assessment='needs_clarification',missing_information=['What is your actual contribution schedule?'])]:
            d=GeminiPlanner(MockGemini(result=result)).run(self.ctx,goal_ref='goal-1',question='Assess')
            self.assertFalse(d['result']['change_recommended'])
    def test_call_limits_and_unrecognised_tools_cannot_execute_code(self):
        class Loop(MockGemini):
            def generate(self,*args):return tool_response([{'goal_ref':'goal-1','contribution':600}],unknown=True,finish=False)
        with self.assertRaises(PlannerError):GeminiPlanner(Loop(),max_tool_calls=1).run(self.ctx,goal_ref='goal-1',question='Assess')
        with self.assertRaises(PlannerError):GeminiPlanner(MockGemini(),timeout_seconds=0).run(self.ctx,goal_ref='goal-1',question='Assess')
    def test_privacy_and_key_redaction(self):
        mock=MockGemini()
        with patch.dict(os.environ,{'GEMINI_API_KEY':'dummy-secret-key'}):
            GeminiPlanner(mock).run(self.ctx,goal_ref='goal-1',question='dummy-secret-key account 123456789012 email demo@example.com')
        prompt=mock.calls[0][0][0].parts[0].text
        for secret in ['dummy-secret-key','123456789012','demo@example.com','demo-everyday','DEMO PAYROLL','transaction_id']:
            self.assertNotIn(secret,prompt)
        self.assertIn('"schema_version": "1.2"',prompt)
    def test_all_financial_scenarios_are_consumed_without_mutation(self):
        contexts=list(demo_contexts().items())
        for c in scenarios():
            contexts.append((c['name'],build_planning_context(c['txns'],as_of=c['as_of'],goals=(Goal('x','holiday',4000,0,date(2027,1,1)),),**c['kwargs'])))
        for name,ctx in contexts:
            with self.subTest(name=name):
                before=deepcopy(ctx);d=GeminiPlanner(MockGemini()).run(ctx,goal_ref='goal-1',question='Assess these financial facts')
                self.assertEqual(ctx,before);self.assertFalse(d['activation_allowed'])

class EvaluationTests(unittest.TestCase):
    def test_quality_cases_have_contrasting_evidence_and_honest_review_limits(self):
        from evaluation.planner_scenarios import cases,review
        fixture_cases=cases()
        self.assertEqual(len(fixture_cases),15)
        indexed={c['name']:c for c in fixture_cases}
        for name in ['normal','vet_paid','vet_unpaid','missing_salary','reduced_salary','delayed_salary',
                'planned_holiday','irregular_freelance','persistent_essential_increase','multiple_goals',
                'unrealistic_deadline','no_material_change','protected_essentials']:
            self.assertIn(name,indexed)
        c=indexed['vet_paid']
        d=GeminiPlanner(MockGemini()).run(c['context'],goal_ref='goal-1',question=c['question'])
        result=review(c,d)
        self.assertFalse(d['model_generated'])
        self.assertTrue(result['manual_review_required'])
        self.assertTrue(result['checks']['source_allocations_preserved'])

class TransportTests(unittest.TestCase):
    def test_environment_key_model_and_backend_only_sdk_settings(self):
        with patch.dict(os.environ,{'GEMINI_API_KEY':'dummy-key','GEMINI_MODEL':'configured-model'}),patch('planning.gemini.genai.Client') as client:
            t=GeminiTransport(env_file=None);t.generate([],types.GenerateContentConfig(),4000)
            self.assertEqual(t.model,'configured-model')
            self.assertEqual(client.call_args.kwargs['api_key'],'dummy-key')
            self.assertEqual(client.call_args.kwargs['http_options'].retry_options.attempts,1)
    def test_missing_key_timeout_rate_limit_and_server_error_are_sanitised(self):
        with patch.dict(os.environ,{},clear=True):
            with self.assertRaises(PlannerError) as cm:GeminiTransport(env_file=None).generate([],types.GenerateContentConfig(),10)
            self.assertEqual(cm.exception.code,'planner_unconfigured')
        for error,code in [(TimeoutError('secret'),'planner_timeout'),(httpx.ReadTimeout('secret'),'planner_timeout'),
            (errors.APIError(429,{'error':{'message':'secret'}}),'planner_rate_limited'),
            (errors.APIError(503,{'error':{'message':'secret'}}),'planner_provider_error')]:
            with patch.dict(os.environ,{'GEMINI_API_KEY':'dummy-key'}):
                t=GeminiTransport(env_file=None);t.client=MagicMock();t.client.models.generate_content.side_effect=error
                with self.assertRaises(PlannerError) as cm:t.generate([],types.GenerateContentConfig(),10)
                self.assertEqual(cm.exception.code,code);self.assertNotIn('secret',cm.exception.message)

class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=DemoStore(Path(self.temp.name)/'bank.sqlite');self.customer='customer'
        self.raw={'txns':[], 'as_of':AS_OF,'accounts':(AccountSnapshot('cash','transaction',2000,AS_OF),AccountSnapshot('s','savings',1200,AS_OF,goal_available=1200))}
        self.bank=BankingService(self.store,lambda customer:self.raw)
        self.bank.create_goal(self.customer,Goal('goal-id','holiday',6000,1200,date(2027,5,31)),confirmed=True,expected_revision=0)
        self.mock=MockGemini();self.planner=PlannerService(self.store,self.mock,cooldown_seconds=0)
    def ask(self, **changes):
        return self.planner.generate(self.bank,self.customer,**{'goal_id':'goal-id','question':'What if I save less?','request_id':'request-1','expected_revision':1,**changes})
    def test_drafts_persist_retrieve_follow_up_and_idempotency_without_goal_changes(self):
        before=self.store.read(self.customer,AS_OF)
        d=self.ask();again=self.ask();self.assertEqual(d,again);self.assertEqual(len(self.mock.calls),2)
        other=PlannerService(self.store,self.mock,cooldown_seconds=0)
        self.assertEqual(other.draft(self.bank,self.customer,d['draft_id'])['status'],'draft_unverified')
        self.ask(request_id='follow-up',session_id=d['session_id'],question='Why that deadline?')
        payload=json.loads(self.mock.calls[-2][0][0].parts[0].text)
        self.assertEqual(len(payload['recent_conversation']),1)
        self.assertEqual(self.store.read(self.customer,AS_OF),before)
        with self.assertRaises(ValueError):self.bank.decide_proposal(self.customer,d['draft_id'],'approve',confirmed=True,expected_revision=1)
        with self.assertRaises(ValueError):self.bank.propose_goal_change(self.customer,'goal-id',{'target':5000},expected_revision=1,source='planner')
    def test_foreign_customer_goal_session_and_draft_are_rejected(self):
        d=self.ask()
        with self.assertRaises(ValueError):self.planner.draft(self.bank,'other',d['draft_id'])
        with self.assertRaises(ValueError):self.ask(request_id='foreign',session_id='unknown')
        with self.assertRaises(ValueError):self.ask(goal_id='foreign')
    def test_changed_context_and_scope_reject_generation_and_mark_stored_draft_stale(self):
        d=self.ask()
        self.bank.create_goal(self.customer,Goal('second','emergency_fund',1000,0,date(2027,5,31)),confirmed=True,expected_revision=1)
        self.assertTrue(self.planner.draft(self.bank,self.customer,d['draft_id'])['stale'])
        with self.assertRaises(StaleState):self.ask(request_id='new')
        with self.assertRaises(StaleState):self.ask(request_id='scope-change',expected_revision=2,still_authorised=lambda:False)
    def test_changed_bank_data_during_model_call_is_rejected(self):
        self.mock.change=lambda:self.raw.update(accounts=(AccountSnapshot('cash','transaction',150,AS_OF),))
        with self.assertRaises(StaleState):self.ask()
        self.assertEqual(self.planner.store.history(self.customer)[0]['status'],'stale')
        self.assertNotIn('draft',self.planner.store.history(self.customer)[0])
    def test_provider_failure_is_persisted_safely_and_not_retried(self):
        self.mock.error=PlannerError('planner_rate_limited','Wait for quota',429)
        for _ in range(2):
            with self.assertRaises(PlannerError):self.ask()
        self.assertEqual(len(self.mock.calls),1)
        self.assertEqual(self.store.read(self.customer,AS_OF)[0],1)
    def test_rate_and_busy_limits(self):
        self.planner.cooldown=60;self.ask()
        with self.assertRaises(PlannerError):self.ask(request_id='another')
        self.planner.cooldown=0;self.planner.gate.acquire()
        try:
            with self.assertRaises(PlannerError):self.ask(request_id='busy')
        finally:self.planner.gate.release()

class PlannerApiTests(unittest.TestCase):
    def test_scoped_api_draft_round_trip_and_no_activation_path(self):
        from api import create_app
        with TemporaryDirectory() as folder:
            app=create_app(Path(folder)/'api.sqlite',planner_transport=MockGemini())
            client=TestClient(app);self.assertEqual(client.get('/api/planner/status').status_code,401)
            client.post('/api/session',json={'customer_id':'u003','demo_acknowledged':True})
            view=client.get('/api/overview').json()
            view=client.post('/api/scenario',headers={'X-Context-Scope':view['scope_version']},json={'scenario':'normal'}).json()
            headers={'X-Context-Scope':view['scope_version']}
            body={'goal_id':'demo-holiday','question':'Assess this goal','request_id':'api-request','expected_revision':view['revision']}
            self.assertEqual(client.post('/api/planner/assess',json=body).status_code,409)
            response=client.post('/api/planner/assess',headers=headers,json=body)
            self.assertEqual(response.status_code,200,response.text)
            d=response.json();self.assertEqual(d['status'],'draft_unverified')
            self.assertFalse(d['activation_allowed'])
            self.assertEqual(client.get('/api/planner/drafts/'+d['draft_id']).status_code,200)
            self.assertEqual(client.get('/api/planner/sessions/'+d['session_id']).status_code,200)
            self.assertEqual(client.post('/api/proposals/'+d['draft_id']+'/decision',headers=headers,
                json={'decision':'approve','confirmed':True,'expected_revision':view['revision']}).status_code,400)
            self.assertEqual(client.get('/api/overview').json()['goals'],view['goals'])
            client.post('/api/scenario',headers=headers,json={'scenario':'vet_unpaid'})
            self.assertEqual(client.post('/api/planner/assess',headers=headers,json={**body,'request_id':'old-scope'}).status_code,409)


class JsonTextModeTests(unittest.TestCase):
    def test_optional_encoding_preserves_calculations_and_history(self):
        context=demo_contexts()['normal'];mock=MockGemini()
        draft=GeminiPlanner(mock,final_output_mode='json_text').run(context,goal_ref='goal-1',question='Assess')
        contents,config,_=mock.calls[-1]
        self.assertIsNone(config.response_json_schema)
        self.assertIsNone(config.response_schema)
        self.assertIsNone(config.response_mime_type)
        self.assertIsNone(config.tools)
        self.assertIn(json.dumps(PlannerResult.model_json_schema(),separators=(',',':')),contents[-1].parts[0].text)
        self.assertTrue(any(p.function_response for c in contents for p in c.parts))
        self.assertEqual(draft['metadata']['final_output_mode'],'json_text')
        self.assertEqual(draft['metadata']['generation_attempts'][-1]['stage'],'json_final')
        self.assertEqual(draft['calculations']['calculation-1']['plans'][0]['contribution'],600)
        self.assertFalse(draft['activation_allowed'])
        self.assertEqual(len(mock.calls),2)

    def test_default_retains_api_schema_and_explicit_selection(self):
        with patch.dict(os.environ,{'GEMINI_FINAL_OUTPUT_MODE':'structured'}):
            mock=MockGemini()
            GeminiPlanner(mock).run(demo_contexts()['normal'],goal_ref='goal-1',question='Assess')
            self.assertEqual(mock.calls[-1][1].response_json_schema,PlannerResult.model_json_schema())
            self.assertEqual(GeminiPlanner(mock,final_output_mode='json_text').final_output_mode,'json_text')
        with patch.dict(os.environ,{'GEMINI_FINAL_OUTPUT_MODE':'unsupported'}):
            with self.assertRaises(PlannerError):GeminiPlanner(MockGemini())

    def test_invalid_output_is_rejected_without_repair_when_corrections_disabled(self):
        missing=output();del missing['answer']
        invalid=['not json','```json\n'+json.dumps(output())+'\n```',missing,
                 {**output(),'activate':True},{**output(),'goal_id':'foreign'},
                 output(change_recommended='true'),output(('foreign',)),output(shock_refs=['foreign'])]
        for value in invalid:
            with self.subTest(value=value):
                mock=MockGemini(result=value)
                with self.assertRaises(PlannerError) as caught:
                    GeminiPlanner(mock,final_output_mode='json_text',max_corrections=0).run(demo_contexts()['normal'],goal_ref='goal-1',question='Assess')
                self.assertEqual(caught.exception.code,'planner_invalid_response')
                self.assertEqual(len(mock.calls),2)
        with self.assertRaises(PlannerError) as caught:
            GeminiPlanner(MockGemini(result=missing),final_output_mode='json_text',max_corrections=0).run(demo_contexts()['normal'],goal_ref='goal-1',question='Assess')
        self.assertIn('proposal_schema',caught.exception.message)

class JsonTextApiTests(PlannerApiTests):
    def setUp(self):
        selected=patch.dict(os.environ,{'GEMINI_FINAL_OUTPUT_MODE':'json_text'})
        selected.start();self.addCleanup(selected.stop)

class JsonTextPersistenceTests(PersistenceTests):
    def setUp(self):
        selected=patch.dict(os.environ,{'GEMINI_FINAL_OUTPUT_MODE':'json_text'})
        selected.start();self.addCleanup(selected.stop)
        super().setUp()
