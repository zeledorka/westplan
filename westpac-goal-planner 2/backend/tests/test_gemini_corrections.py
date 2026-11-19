"""Deterministic correction tests; all model responses are labelled test doubles."""
from copy import deepcopy
import json
import os
import unittest
from unittest.mock import patch
from google.genai import types
from planning.agent import GeminiPlanner
from planning.agent_tools import PlanningTools
from planning.gemini import PlannerError, GenerationRetryPolicy
from planning.proposal_validation import (Category, ProposalValidationError, validate_proposal, date_facts)
from tests.test_gemini_planner import MockGemini, output, tool_response
from planning.demo import demo_contexts, AS_OF


def proposal(amount=450, bad_deadline=False):
    obj=output()
    obj['strategies'][0]['approach']=f'Contribute AUD {amount} monthly.'
    obj['strategies'][0]['rationale']='Achieves the goal exactly on/at the deadline.' if bad_deadline else 'Preserves protected funds; review the Python dates and constraints.'
    return obj


class Scripted(MockGemini):
    def __init__(self, results, amount=600):
        super().__init__(alternatives=False)
        self.results=list(results);self.amount=amount
    def generate(self, contents, config, timeout_ms):
        self.calls.append((deepcopy(contents),deepcopy(config),timeout_ms))
        if config.tools:
            return tool_response([{'goal_ref':'goal-1','contribution':self.amount}])
        value=self.results.pop(0)
        if isinstance(value,Exception):raise value
        raw=value if isinstance(value,str) else json.dumps(value)
        return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=[types.Part.from_text(text=raw)]))])


class CorrectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.context=demo_contexts()['normal']
    def run_agent(self, transport, **kwargs):
        agent=GeminiPlanner(transport,retry_policy=GenerationRetryPolicy(max_retries=0),**kwargs)
        result=agent.run(self.context,goal_ref='goal-1',question='Assess')
        return result,agent

    def test_A_exact_deadline_feedback_then_complete_revalidation_both_modes(self):
        for mode in ['structured','json_text']:
            with self.subTest(mode=mode):
                original=proposal(600,True);corrected=proposal(600)
                corrected['strategies'][0]['rationale']='Completion is 2027-05-30, before the requested deadline of 2027-05-31.'
                t=Scripted([original,corrected]);draft,agent=self.run_agent(t,final_output_mode=mode)
                feedback=json.loads(t.calls[-1][0][-1].parts[0].text)
                issue=feedback['validation_errors'][0]
                self.assertEqual(issue['field'],'strategies[0].rationale')
                self.assertEqual(issue['original_claim'],original['strategies'][0]['rationale'])
                self.assertEqual(issue['calculation_ref'],'calculation-1')
                self.assertEqual(issue['authoritative_values']['plans'][0]['deadline_relation'],'before')
                self.assertEqual(issue['authoritative_values']['plans'][0]['completion_date'],'2027-05-30')
                self.assertEqual(json.loads(feedback['original_proposal']),original)
                self.assertEqual(draft['result'],corrected)
                self.assertFalse(draft['metadata']['initial_output_valid'])
                self.assertEqual(draft['metadata']['correction_attempts'],1)
                self.assertTrue(draft['metadata']['correction_succeeded'])
                for _,config,_ in t.calls[1:]:
                    self.assertEqual(config.tool_config.function_calling_config.mode,'NONE')
                    self.assertEqual(bool(config.response_json_schema),mode=='structured')

    def test_B_wrong_monetary_claim_must_match_python(self):
        t=Scripted([proposal(500),proposal(450)],amount=450)
        draft,_=self.run_agent(t,final_output_mode='json_text')
        self.assertEqual(draft['calculations']['calculation-1']['plans'][0]['contribution'],450)
        self.assertIn('monetary_claim_mismatch',draft['metadata']['validation_error_categories'])
        self.assertEqual(draft['metadata']['correction_attempts'],1)

    def test_C_same_error_stops_at_configured_limit(self):
        for limit in [0,1,2]:
            with self.subTest(limit=limit):
                t=Scripted([proposal(600,True)]*3)
                agent=GeminiPlanner(t,max_corrections=limit)
                with self.assertRaises(ProposalValidationError):agent.run(self.context,goal_ref='goal-1',question='Assess')
                self.assertEqual(agent.last_metrics['correction_attempts'],limit)
                self.assertEqual(len(t.calls),2+limit)
                self.assertFalse(agent.last_metrics['success'])

    def test_D_deadline_fix_cannot_hide_new_wrong_amount(self):
        initial=proposal(450,True);introduced=proposal(500);corrected=proposal(450)
        t=Scripted([initial,introduced,corrected],amount=450)
        draft,_=self.run_agent(t)
        self.assertEqual(draft['metadata']['correction_attempts'],2)
        self.assertEqual(set(draft['metadata']['validation_error_categories']),{'deadline_precision_mismatch','monetary_claim_mismatch'})
        self.assertEqual(draft['result'],corrected)

    def test_E_valid_initial_has_no_correction(self):
        t=Scripted([proposal(600)]);draft,_=self.run_agent(t)
        self.assertEqual(len(t.calls),2)
        self.assertTrue(draft['metadata']['initial_output_valid'])
        self.assertEqual(draft['metadata']['correction_attempts'],0)

    def test_extra_change_requested_requires_model_correction_not_silent_removal(self):
        for mode in ['structured','json_text']:
            for value in [False,True]:
                with self.subTest(mode=mode,value=value):
                    corrected=proposal(600)
                    # Match the live failure: request metadata and recommendation disagree.
                    corrected['change_recommended']=not value
                    original={**corrected,'change_requested':value}
                    t=Scripted([original,corrected])
                    with patch.object(PlanningTools,'calculate_strategy',autospec=True,
                                      side_effect=PlanningTools.calculate_strategy) as calculate:
                        draft,_=self.run_agent(t,final_output_mode=mode)
                    self.assertEqual(calculate.call_count,1)
                    self.assertEqual(len(t.calls),3)
                    feedback=json.loads(t.calls[-1][0][-1].parts[0].text)
                    issue=feedback['validation_errors'][0]
                    self.assertEqual(issue['error_type'],'proposal_schema')
                    self.assertEqual(issue['field'],'change_requested')
                    self.assertEqual(issue['original_claim'],json.dumps(value))
                    self.assertIn('Remove the unsupported top-level boolean field',issue['correction_required'])
                    self.assertIn('Do not rename it or copy its value',issue['correction_required'])
                    self.assertEqual(json.loads(feedback['original_proposal']),original)
                    self.assertEqual(feedback['authoritative_python_calculations'],draft['baseline_calculations'])
                    self.assertEqual(draft['result'],corrected)
                    self.assertNotIn('change_requested',draft['result'])
                    self.assertFalse(draft['metadata']['initial_output_valid'])
                    self.assertEqual(draft['metadata']['correction_attempts'],1)
                    self.assertTrue(draft['metadata']['correction_succeeded'])
                    self.assertEqual(draft['metadata']['correction_calculation_calls'],0)
                    self.assertEqual(draft['status'],'draft_unverified')
                    self.assertFalse(draft['activation_allowed'])
                    for _,config,_ in t.calls[1:]:
                        self.assertEqual(config.tool_config.function_calling_config.mode,'NONE')

    def test_extra_change_requested_is_still_invalid_and_bounded(self):
        original={**proposal(600),'change_requested':False}
        for limit in [0,1,2]:
            with self.subTest(limit=limit):
                t=Scripted([original]*3);agent=GeminiPlanner(t,max_corrections=limit)
                with self.assertRaises(ProposalValidationError) as cm:
                    agent.run(self.context,goal_ref='goal-1',question='Assess')
                self.assertTrue(cm.exception.recoverable)
                self.assertEqual(cm.exception.issues[0].field,'change_requested')
                self.assertEqual(agent.last_metrics['correction_attempts'],limit)
                self.assertEqual(len(t.calls),2+limit)
                self.assertFalse(agent.last_metrics['success'])

    def test_change_requested_exception_cannot_admit_unknown_fields_or_actions(self):
        nested=proposal(600);nested['strategies'][0]['change_requested']=False
        cases=[nested,{**proposal(600),'unexpected_metadata':False},
               *[{**proposal(600),'change_requested':value} for value in ['false',0,None,{'activate':True}]],
               {**proposal(600),'change_requested':False,'activate':True},
               {**proposal(600),'change_requested':False,'customer_id':'foreign'},
               {**proposal(600),'change_requested':False,'contribution':1}]
        for obj in cases:
            with self.subTest(obj=obj):
                t=Scripted([obj]);agent=GeminiPlanner(t)
                with self.assertRaises(ProposalValidationError) as cm:
                    agent.run(self.context,goal_ref='goal-1',question='Assess')
                self.assertFalse(cm.exception.recoverable)
                self.assertEqual(len(t.calls),2)
                self.assertEqual(agent.last_metrics['correction_attempts'],0)

    def test_change_requested_correction_revalidates_all_financial_claims(self):
        original={**proposal(600),'change_requested':False}
        t=Scripted([original,proposal(500),proposal(600)])
        draft,_=self.run_agent(t)
        self.assertEqual(draft['metadata']['correction_attempts'],2)
        self.assertEqual(set(draft['metadata']['validation_error_categories']),{'proposal_schema','monetary_claim_mismatch'})
        self.assertEqual(draft['result'],proposal(600))
        uncalculated=proposal(600);uncalculated['strategies'][0]['contribution']=500
        t=Scripted([original,uncalculated]);agent=GeminiPlanner(t)
        with self.assertRaises(ProposalValidationError):
            agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertTrue(agent.last_metrics['new_calculations_required'])
        self.assertFalse(agent.last_metrics['success'])

    def test_change_requested_removal_still_requires_precise_surplus_wording(self):
        ambiguous=proposal(600)
        ambiguous['answer']='The comfortable monthly surplus is AUD 900.'
        corrected=deepcopy(ambiguous)
        corrected['answer']='The historical monthly surplus is AUD 900. The remaining monthly surplus is AUD 300.'
        t=Scripted([{**ambiguous,'change_requested':False},ambiguous,corrected])
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,
                          side_effect=PlanningTools.calculate_strategy) as calculate:
            draft,_=self.run_agent(t)
        self.assertEqual(calculate.call_count,1)
        self.assertEqual(draft['metadata']['correction_attempts'],2)
        self.assertEqual(set(draft['metadata']['validation_error_categories']),{'proposal_schema','monetary_claim_mismatch'})
        self.assertEqual(draft['result'],corrected)

    def test_F_nonrecoverable_values_fail_without_correction(self):
        cases=[{**proposal(600),'activate':True},{**proposal(600),'customer_id':'foreign'},
               {**proposal(600),'calculations':{'contribution':1}},output(('foreign',))]
        action=proposal(600);action['strategies'][0]['proposed_actions']=[{'execute':'payment'}];cases.append(action)
        action=proposal(600);action['strategies'][0]['proposed_actions']=['Activate this goal now.'];cases.append(action)
        for obj in cases:
            with self.subTest(obj=obj):
                t=Scripted([obj]);agent=GeminiPlanner(t)
                with self.assertRaises(ProposalValidationError) as cm:agent.run(self.context,goal_ref='goal-1',question='Assess')
                self.assertFalse(cm.exception.recoverable);self.assertEqual(len(t.calls),2)
                self.assertEqual(agent.last_metrics['correction_attempts'],0)

    def test_G_provider_failure_during_correction_is_preserved(self):
        for code in [503,504]:
            t=Scripted([proposal(600,True),PlannerError('planner_provider_error','Unavailable',502,upstream_status=code)])
            agent=GeminiPlanner(t,retry_policy=GenerationRetryPolicy(max_retries=0))
            with self.assertRaises(PlannerError) as cm:agent.run(self.context,goal_ref='goal-1',question='Assess')
            self.assertEqual(cm.exception.upstream_status,code)
            self.assertEqual(cm.exception.code,'planner_provider_error')
            self.assertEqual(agent.last_metrics['final_outcome'],'provider_failure')
            self.assertEqual(agent.last_metrics['correction_attempts'],1)
            self.assertFalse(agent.last_metrics['success'])

    def test_H_wording_correction_preserves_ledger_and_strategy(self):
        t=Scripted([proposal(600,True),proposal(600)])
        original=PlanningTools.calculate_strategy
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=original) as calculate:
            draft,_=self.run_agent(t)
        self.assertEqual(calculate.call_count,1)
        self.assertEqual(draft['metadata']['correction_calculation_calls'],0)
        self.assertTrue(draft['metadata']['wording_only_correction'])
        self.assertFalse(draft['metadata']['strategies_changed'])
        final_contents=t.calls[-1][0]
        responses=[p.function_response.response for c in final_contents for p in c.parts if p.function_response and p.function_response.name=='calculate_strategy']
        self.assertEqual(responses,[draft['calculations']['calculation-1']])
        self.assertFalse(draft['activation_allowed']);self.assertEqual(draft['status'],'draft_unverified')

    def test_I_budget_and_deadline_are_shared(self):
        t=Scripted([proposal(600,True)]);agent=GeminiPlanner(t,max_model_calls=2)
        with self.assertRaises(PlannerError) as cm:agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertEqual(cm.exception.code,'planner_limit');self.assertEqual(len(t.calls),2)
        self.assertEqual(agent.last_metrics['correction_attempts'],0)
        t=Scripted([proposal(600,True)]);agent=GeminiPlanner(t,retry_policy=GenerationRetryPolicy(max_attempts=2))
        with self.assertRaises(PlannerError):agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertEqual(len(t.calls),2)
        clock=[0]
        class Slow(Scripted):
            def generate(self,*args):
                result=super().generate(*args)
                if not args[1].tools:clock[0]+=2
                return result
        t=Slow([proposal(600,True)]);agent=GeminiPlanner(t,clock=lambda:clock[0],timeout_seconds=1)
        with self.assertRaises(PlannerError):agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertEqual(len(t.calls),2)
        clock[0]=0
        t=Scripted([proposal(600)]);agent=GeminiPlanner(t,clock=lambda:clock[0],timeout_seconds=1)
        from planning.proposal_validation import validate_proposal as real_validate
        def validation_takes_time(*args):
            result=real_validate(*args);clock[0]=2;return result
        with patch('planning.agent.validate_proposal',side_effect=validation_takes_time),self.assertRaises(PlannerError) as cm:
            agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertEqual(cm.exception.code,'planner_limit');self.assertFalse(agent.last_metrics['success'])

    def test_format_recovery_and_entire_schema_are_rechecked(self):
        t=Scripted(['```json\ninvalid\n```',proposal(600)])
        draft,_=self.run_agent(t)
        self.assertEqual(draft['metadata']['correction_attempts'],1)
        t=Scripted([proposal(600,True),{**proposal(600),'activate':True}]);agent=GeminiPlanner(t)
        with self.assertRaises(ProposalValidationError) as cm:agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertFalse(cm.exception.recoverable);self.assertEqual(len(t.calls),3)

    def test_explicit_new_parameters_are_not_accepted_without_calculation(self):
        obj=proposal(600);obj['strategies'][0]['contribution']=500
        t=Scripted([proposal(600,True),obj]);agent=GeminiPlanner(t)
        with self.assertRaises(ProposalValidationError):agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertTrue(agent.last_metrics['new_calculations_required'])
        self.assertEqual(len(t.calls),3)

    def test_date_and_feasibility_claims_checked(self):
        c=PlanningTools(self.context);c.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':450}]})
        for obj,category in [(proposal(450),None)]:validate_proposal(json.dumps(obj),c.calculations,self.context)
        obj=proposal(450);obj['strategies'][0]['rationale']='This strategy meets the deadline.'
        with self.assertRaises(ProposalValidationError) as cm:validate_proposal(json.dumps(obj),c.calculations,self.context)
        self.assertIn(Category.FEASIBILITY,[i.error_type for i in cm.exception.issues])
        obj=proposal(450);obj['strategies'][0]['rationale']='The completion date is 2027-05-31.'
        with self.assertRaises(ProposalValidationError) as cm:validate_proposal(json.dumps(obj),c.calculations,self.context)
        self.assertIn(Category.DATE,[i.error_type for i in cm.exception.issues])
        self.assertEqual(date_facts(c.calculations)['calculation-1'][0]['deadline_relation'],'after')

    def test_required_amount_is_not_confused_with_chosen_contribution(self):
        tools=PlanningTools(self.context);tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':450}]})
        obj=proposal(450);obj['strategies'][0]['rationale']='The required monthly contribution is AUD 600. The chosen contribution is AUD 450 monthly and finishes later.'
        validate_proposal(json.dumps(obj),tools.calculations,self.context)
        obj['strategies'][0]['rationale']='The remaining monthly surplus is AUD 500.'
        with self.assertRaises(ProposalValidationError) as cm:validate_proposal(json.dumps(obj),tools.calculations,self.context)
        self.assertIn(Category.MONEY,[i.error_type for i in cm.exception.issues])

    def test_all_deadline_relations_use_python_dates(self):
        for amount,deadline,relation in [(600,'2027-05-31','before'),(600,'2027-05-30','on'),(450,'2027-05-31','after'),(0,'2027-05-31','unknown')]:
            tools=PlanningTools(self.context)
            tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':amount,'deadline':deadline}]})
            self.assertEqual(date_facts(tools.calculations)['calculation-1'][0]['deadline_relation'],relation)

    def test_configuration_and_duplicate_fields_fail_closed(self):
        for value in ['-1','5','invalid']:
            with patch.dict(os.environ,{'GEMINI_MAX_CORRECTIONS':value}),self.assertRaises(PlannerError):GeminiPlanner(MockGemini())
        with patch.dict(os.environ,{'GEMINI_MAX_CORRECTIONS':'4'}):
            self.assertEqual(GeminiPlanner(MockGemini()).max_corrections,4)
        t=Scripted(['{"answer":"one","answer":"two"}']);agent=GeminiPlanner(t)
        with self.assertRaises(ProposalValidationError) as cm:agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertFalse(cm.exception.recoverable);self.assertEqual(len(t.calls),2)


class CorrectionPersistenceTests(unittest.TestCase):
    def setUp(self):
        from tests import test_gemini_planner as fixtures
        fixtures.PersistenceTests.setUp(self)
    def ask(self,**kwargs):
        from tests import test_gemini_planner as fixtures
        return fixtures.PersistenceTests.ask(self,**kwargs)

    def test_only_final_validated_draft_is_saved_and_cannot_activate(self):
        before=self.store.read(self.customer,AS_OF)
        t=Scripted([proposal(600,True),proposal(600)])
        self.planner.transport=t
        draft=self.ask()
        self.assertEqual(draft['status'],'draft_unverified')
        self.assertTrue(draft['metadata']['correction_succeeded'])
        rows=self.planner.store.history(self.customer)
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['draft']['result'],proposal(600))
        self.assertEqual(self.store.read(self.customer,AS_OF),before)
        with self.assertRaises(ValueError):self.bank.decide_proposal(self.customer,draft['draft_id'],'approve',confirmed=True,expected_revision=1)
        with self.assertRaises(ValueError):self.planner.draft(self.bank,'foreign',draft['draft_id'])

    def test_corrected_change_requested_only_saves_valid_draft_without_goal_changes(self):
        before=self.store.read(self.customer,AS_OF)
        corrected=proposal(600);corrected['change_recommended']=True
        self.planner.transport=Scripted([{**corrected,'change_requested':False},corrected])
        draft=self.ask()
        rows=self.planner.store.history(self.customer)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['draft']['result'],corrected)
        self.assertEqual(draft['metadata']['correction_attempts'],1)
        self.assertEqual(self.store.read(self.customer,AS_OF),before)
        self.assertFalse(draft['activation_allowed'])
        self.assertEqual(draft['status'],'draft_unverified')
        with self.assertRaises(ValueError):
            self.bank.decide_proposal(self.customer,draft['draft_id'],'approve',confirmed=True,expected_revision=1)

    def test_scope_and_financial_context_are_rechecked_after_correction(self):
        from planning.store import StaleState
        from planning.schemas import AccountSnapshot
        from planning.demo import AS_OF
        fixture=self
        class Changed(Scripted):
            def generate(self,*args):
                response=super().generate(*args)
                if not args[1].tools and not self.results:
                    fixture.raw.update(accounts=(AccountSnapshot('cash','transaction',150,AS_OF),))
                return response
        self.planner.transport=Changed([proposal(600,True),proposal(600)])
        with self.assertRaises(StaleState):self.ask()
        self.assertEqual(self.planner.store.history(self.customer)[0]['status'],'stale')
        self.assertNotIn('draft',self.planner.store.history(self.customer)[0])

    def test_correction_provider_failure_persists_failure_only(self):
        t=Scripted([proposal(600,True),PlannerError('planner_provider_error','Unavailable',502,upstream_status=503)])
        self.planner.transport=t
        with patch.dict(os.environ,{'GEMINI_MAX_RETRIES':'0'}),self.assertRaises(PlannerError):self.ask()
        row=self.planner.store.request(self.customer,'request-1')
        self.assertEqual(row['status'],'interrupted');self.assertNotIn('draft',row)
        self.assertEqual(row['metadata']['correction_attempts'],1)
        self.assertEqual(row['metadata']['final_outcome'],'provider_failure')
        self.assertEqual(row['error']['code'],'planner_provider_error')
