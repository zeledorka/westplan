"""Automatic safety revision tests: real Python tools/checker, scripted Gemini only."""
from copy import deepcopy
from datetime import date
from dataclasses import replace
import json,os,unittest
from unittest.mock import patch
from google.genai import types
from planning.agent import GeminiPlanner
from planning.agent_tools import PlanningTools
from planning.safety import assess_draft
from planning.gemini import PlannerError,GenerationRetryPolicy
from planning.checkpoint import unpack
from planning.demo import demo_contexts,demo_transactions,AS_OF
from planning.schemas import AccountSnapshot
from planning.store import StaleState
from tests.test_gemini_planner import output
from tests.test_gemini_retries import Clock


def tool(*plans,finish=True):
    parts=[types.Part.from_function_call(name='calculate_strategy',args={'plans':[p]}) for p in plans]
    if finish:parts.append(types.Part.from_function_call(name='finish_planning',args={}))
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=parts))])


def plan(amount,**kw):return {'goal_ref':'goal-1','contribution':amount,**kw}


class Transport:
    available=True;model='mock-safety-revision';provider='mock'
    def __init__(self,*responses,hook=None):self.responses=list(responses);self.calls=[];self.hook=hook
    def generate(self,contents,config,timeout_ms):
        self.calls.append((deepcopy(contents),deepcopy(config),timeout_ms))
        value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        if not isinstance(value,types.GenerateContentResponse):
            raw=value if isinstance(value,str) else json.dumps(value)
            value=types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=[types.Part.from_text(text=raw)]))])
        if self.hook:self.hook(self,config)
        return value


class SafetyRevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.ctx=demo_contexts()['normal']
    def run_agent(self,t,ctx=None,**kw):
        ctx=ctx or self.ctx;clock=Clock()
        a=GeminiPlanner(t,clock=clock,sleeper=clock.sleep,jitter=lambda low,high:high,final_output_mode=kw.pop('final_output_mode','json_text'),**kw)
        checker=lambda d:assess_draft(d,ctx,d['baseline_calculations'],expected_goal_ref='goal-1')
        return a,checker,clock
    def ask(self,t,ctx=None,**kw):
        a,c,clock=self.run_agent(t,ctx,**kw)
        return a.run(ctx or self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c),a,clock

    def test_pass_and_block_do_not_request_safety_revision(self):
        for blocked in [False,True]:
            ctx=deepcopy(self.ctx)
            if blocked:ctx['financial_insights']['income']['variability_cv']=None
            t=Transport(tool(plan(600)),output())
            d,_,_=self.ask(t,ctx)
            self.assertEqual(len(t.calls),2)
            self.assertEqual(d['safety_assessment']['status'],'BLOCK' if blocked else 'PASS')
            self.assertEqual(d['safety_revision']['revision_count'],0)

    def test_revise_fresh_calculation_and_independent_pass_keeps_original(self):
        t=Transport(tool(plan(1100)),output(),tool(plan(600)),output(('calculation-2',)))
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calc:
            d,a,_=self.ask(t)
        self.assertEqual(calc.call_count,2)
        self.assertEqual(d['safety_assessment']['status'],'PASS')
        self.assertEqual(d['metadata']['safety_revision_attempts'],1)
        self.assertEqual(d['metadata']['safety_revision_calculation_calls'],1)
        self.assertEqual(d['metadata']['correction_attempts'],0)
        history=d['safety_revision']['history'];self.assertEqual(len(history),2)
        self.assertEqual(history[0]['safety_assessment']['status'],'REVISE')
        self.assertEqual(history[0]['proposal']['strategies'][0]['calculation_ref'],'calculation-1')
        self.assertEqual(history[1]['new_calculation_refs'],['calculation-2'])
        self.assertEqual(d['baseline_calculations']['calculation-1']['plans'][0]['contribution'],1100)
        feedback=json.loads(t.calls[2][0][-1].parts[0].text)
        self.assertEqual(feedback['strategies'][0]['role'],'recommended')
        self.assertEqual(feedback['strategies'][0]['authoritative_evidence']['currency'],'AUD')
        self.assertEqual(feedback['strategies'][0]['findings'][0]['code'],'EXCEEDS_STRETCH_CAPACITY')
        self.assertFalse(d['activation_allowed'])

    def test_structured_output_retains_schema_and_independent_recheck(self):
        t=Transport(tool(plan(1100)),output(),tool(plan(600)),output(('calculation-2',)))
        d,_,_=self.ask(t,final_output_mode='structured')
        self.assertEqual(d['safety_assessment']['status'],'PASS')
        for index in [1,3]:
            self.assertTrue(t.calls[index][1].response_json_schema)
            self.assertEqual(t.calls[index][1].tool_config.function_calling_config.mode,'NONE')

    def test_tight_budget_reserves_a_generation_for_the_revised_final(self):
        t=Transport(tool(plan(1100)),output(),tool(plan(600),finish=False),output(('calculation-2',)))
        d,_,_=self.ask(t,max_model_calls=4)
        self.assertEqual(d['safety_assessment']['status'],'PASS')
        self.assertEqual(len(t.calls),4)

    def test_unchanged_unsafe_stops_after_limit_and_zero_disables(self):
        for limit in [0,1]:
            t=Transport(tool(plan(1100)),output(),tool(plan(1100)),output())
            with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calc:
                d,_,_=self.ask(t,max_safety_revisions=limit)
            self.assertEqual(calc.call_count,1) # Identical request reuses completed tool output.
            self.assertEqual(d['safety_assessment']['status'],'REVISE')
            self.assertEqual(d['safety_revision']['revision_count'],limit)
            self.assertEqual(len(t.calls),2+2*limit)
        for value in [-1,2,True]:
            with self.assertRaises(PlannerError):GeminiPlanner(Transport(),max_safety_revisions=value)

    def test_partial_safety_preserves_passed_option_without_replaying_math(self):
        initial=output(('calculation-2','calculation-1'),recommended_ref='calculation-2')
        t=Transport(tool(plan(1100),plan(600)),initial,tool(plan(600)),output(('calculation-2',)))
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calc:
            d,_,_=self.ask(t)
        self.assertEqual(calc.call_count,2)
        self.assertEqual(d['safety_revision']['new_calculation_refs'],[])
        self.assertEqual(d['metadata']['safety_reused_calculation_refs'],['calculation-2'])
        feedback=json.loads(t.calls[2][0][-1].parts[0].text)
        self.assertEqual([(s['role'],s['status']) for s in feedback['strategies']],[('recommended','PASS'),('alternative','REVISE')])
        self.assertEqual(d['safety_assessment']['status'],'PASS')

    def test_changing_previously_passed_option_creates_new_evidence(self):
        t=Transport(tool(plan(1100),plan(600)),output(('calculation-2','calculation-1')),
                    tool(plan(500,deadline='2027-08-31')),output(('calculation-3',)))
        d,_,_=self.ask(t)
        self.assertEqual(d['safety_revision']['new_calculation_refs'],['calculation-3'])
        self.assertEqual(d['baseline_calculations']['calculation-2']['plans'][0]['contribution'],600)
        self.assertEqual(d['calculations']['calculation-3']['plans'][0]['contribution'],500)
        self.assertEqual(d['safety_assessment']['status'],'PASS')

    def test_invalid_revision_cannot_claim_pass_or_reuse_wrong_reference(self):
        wrong=output();wrong['strategies'][0]['approach']='Contribute AUD 600 monthly.'
        for value in [wrong,output(('invented-ref',)),{**output(('calculation-2',)),'safety_status':'PASS'},'invalid JSON']:
            t=Transport(tool(plan(1100)),output(),tool(plan(600)),value)
            a,c,_=self.run_agent(t,max_corrections=0)
            with self.assertRaises(PlannerError):a.run(self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c)
            self.assertEqual(a.last_safety_revision['history'][0]['safety_assessment']['status'],'REVISE')
            self.assertEqual(len(a.last_safety_revision['history']),1)
            self.assertIsNotNone(a.last_safety_revision['failure'])
            self.assertFalse(a.last_metrics['success'])

    def test_changed_pause_and_deadline_use_python_not_just_cap_clipping(self):
        ctx=demo_contexts()['vet_paid']
        t=Transport(tool(plan(600)),output(),tool(plan(450,pause_months=1,deadline='2027-10-31')),output(('calculation-2',)))
        d,_,_=self.ask(t,ctx)
        p=d['calculations']['calculation-2']['plans'][0]
        self.assertEqual((p['contribution'],p['pause_months'],p['deadline']),(450,1,'2027-10-31'))
        self.assertEqual(d['safety_assessment']['status'],'PASS')

    def test_503_retries_exact_revision_call_not_whole_assessment(self):
        t=Transport(tool(plan(1100)),output(),PlannerError('planner_provider_error','Busy',502,upstream_status=503),tool(plan(600)),output(('calculation-2',)))
        d,_,clock=self.ask(t,retry_policy=GenerationRetryPolicy(1,20,.1,.1))
        self.assertEqual(clock.delays,[.1]);self.assertEqual(d['metadata']['retry_attempts'],1)
        self.assertEqual(t.calls[2][0],t.calls[3][0]);self.assertEqual(d['metadata']['safety_revision_attempts'],1)
        self.assertEqual(len(d['baseline_calculations']),2)

    def test_checkpoint_resume_preserves_new_calculation_and_revision_count(self):
        first=Transport(tool(plan(1100)),output(),tool(plan(600)),PlannerError('planner_provider_error','Busy',502,upstream_status=503))
        a,c,_=self.run_agent(first,retry_policy=GenerationRetryPolicy(max_retries=0))
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calc:
            with self.assertRaises(PlannerError):a.run(self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c)
            state=unpack(a.last_checkpoint['state'])
            self.assertEqual(state['phase'],'safety_final');self.assertEqual(len(state['calculations']),2)
            second=Transport(output(('calculation-2',)));b,c2,_=self.run_agent(second)
            d=b.run(self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c2,checkpoint=a.last_checkpoint)
        self.assertEqual(calc.call_count,2)
        self.assertEqual(len(second.calls),1)
        self.assertEqual(d['safety_revision']['revision_count'],1)
        self.assertEqual(len(d['safety_revision']['history']),2)
        self.assertEqual(d['safety_assessment']['status'],'PASS')

    def test_invalid_revision_tool_arguments_are_bounded_and_recoverable(self):
        t=Transport(tool(plan(1100)),output(),tool(plan('invalid'),finish=False),tool(plan(600)),output(('calculation-2',)))
        d,_,_=self.ask(t)
        self.assertEqual(d['metadata']['calculation_validation_errors'],1)
        self.assertEqual(d['metadata']['safety_revision_calculation_calls'],1)
        self.assertEqual(d['safety_assessment']['status'],'PASS')

    def test_cancellation_and_generation_budget_are_finite(self):
        t=Transport(tool(plan(1100)),output());a,c,_=self.run_agent(t)
        cancelled=[False]
        def progress(p):
            if p['status']=='adjusting_safety':cancelled[0]=True
        with self.assertRaises(PlannerError) as cm:
            a.run(self.ctx,goal_ref='goal-1',question='Assess',safety_checker=c,on_progress=progress,cancelled=lambda:cancelled[0])
        self.assertEqual(cm.exception.code,'planner_cancelled');self.assertEqual(len(t.calls),2)
        self.assertEqual(a.last_safety_revision['outcome'],'cancelled')
        t=Transport(tool(plan(1100)),output());d,_,_=self.ask(t,max_model_calls=2)
        self.assertEqual(d['safety_revision']['outcome'],'budget_exhausted')
        self.assertEqual(d['safety_assessment']['status'],'REVISE');self.assertFalse(d['activation_allowed'])


class SafetyRevisionPersistenceTests(unittest.TestCase):
    def setUp(self):
        from tests.test_gemini_planner import PersistenceTests
        PersistenceTests.setUp(self)
        self.raw.update(txns=[replace(t,user_id=self.customer) for t in demo_transactions()],accounts=(AccountSnapshot('demo-everyday','transaction',2000,date(2026,1,1)),
            AccountSnapshot('demo-savings','savings',9300,AS_OF,protected=8100,goal_available=1200)))
        self.planner.transport=Transport(tool(plan(1100)),output(),tool(plan(600)),output(('calculation-2',)))
    def ask(self):
        from tests.test_gemini_planner import PersistenceTests
        return PersistenceTests.ask(self)

    def test_original_final_findings_persist_and_cannot_activate(self):
        before=self.store.read(self.customer,AS_OF);d=self.ask()
        self.assertEqual(len(d['safety_revision']['history']),2)
        self.assertEqual(d['safety_assessment']['status'],'PASS')
        self.assertEqual(self.ask(),d) # Idempotent; no extra inference.
        restored=self.planner.draft(self.bank,self.customer,d['draft_id'])
        self.assertEqual(restored['safety_revision'],d['safety_revision'])
        with self.assertRaises(ValueError):self.planner.draft(self.bank,'foreign',d['draft_id'])
        with self.assertRaises(ValueError):self.bank.decide_proposal(self.customer,d['draft_id'],'approve',confirmed=True,expected_revision=1)
        self.assertEqual(self.store.read(self.customer,AS_OF),before)

    def test_context_changed_during_revision_cannot_publish(self):
        def changed(t,config):
            if len(t.calls)==4:self.raw.update(accounts=(AccountSnapshot('demo-everyday','transaction',150,AS_OF),))
        self.planner.transport.hook=changed
        with self.assertRaises(StaleState):self.ask()
        row=self.planner.store.request(self.customer,'request-1')
        self.assertEqual(row['status'],'stale');self.assertNotIn('draft',row)
        self.assertIn('safety_revision',row)

    def test_failed_revision_retains_original_findings_without_draft(self):
        self.planner.transport=Transport(tool(plan(1100)),output(),tool(plan(600)),output(('foreign',)))
        with self.assertRaises(PlannerError):self.ask()
        row=self.planner.store.request(self.customer,'request-1')
        self.assertEqual(row['status'],'failed');self.assertNotIn('draft',row)
        public=self.planner.request_status(self.bank,self.customer,'request-1')
        self.assertEqual(public['safety_revision']['history'][0]['safety_assessment']['status'],'REVISE')
        self.assertEqual(public['safety_revision']['calculation_evidence']['calculation-2']['plans'][0]['contribution'],600)
        self.assertNotIn('checkpoint',public)

    def test_durable_resume_keeps_original_findings_and_new_calculations(self):
        self.planner.transport=Transport(tool(plan(1100)),output(),tool(plan(600)),PlannerError('planner_provider_error','Busy',502,upstream_status=503))
        with patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'0'}),self.assertRaises(PlannerError):self.ask()
        row=self.planner.store.request(self.customer,'request-1')
        self.assertEqual(row['status'],'interrupted')
        self.assertEqual(row['safety_revision']['new_calculation_refs'],['calculation-2'])
        self.planner.transport=Transport(output(('calculation-2',)))
        self.planner.retry(self.bank,self.customer,'request-1',expected_revision=1)
        self.planner.jobs[(self.customer,'request-1')].join(30)
        final=self.planner.request_status(self.bank,self.customer,'request-1')
        self.assertEqual(final['status'],'completed',final)
        self.assertEqual(final['draft']['safety_revision']['revision_count'],1)
        self.assertEqual(len(final['draft']['safety_revision']['history']),2)
        self.assertEqual(final['draft']['safety_assessment']['status'],'PASS')
        self.assertIsNone(final['draft']['safety_revision']['failure'])
        self.assertIsNone(final['error'])
        self.assertEqual(len(self.planner.transport.calls),1)
