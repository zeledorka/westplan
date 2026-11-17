"""Independent MVP prudence checks. Gemini transports are labelled test doubles."""
from copy import deepcopy
from datetime import date
import json
import unittest
from unittest.mock import patch
from planning.agent_tools import PlanningTools
from planning.safety import assess_draft
from planning.demo import demo_contexts,AS_OF
from planning.schemas import Goal
from planning.store import StaleState
from tests.test_gemini_planner import output, MockGemini


class FinancialSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.contexts=demo_contexts()
    def make(self,case='normal',plans=None,amount=600,context=None,**changes):
        ctx=deepcopy(context or self.contexts[case]);tools=PlanningTools(ctx)
        tools.calculate_strategy({'plans':plans or [{'goal_ref':'goal-1','contribution':amount}]})
        obj=output();obj.update(changes)
        draft={'status':'draft_unverified','activation_allowed':False,'financial_version':ctx['state_version'],
               'selected_goal_ref':'goal-1','result':obj,'calculations':deepcopy(tools.calculations),
               'baseline_calculations':deepcopy(tools.calculations)}
        return draft,ctx,tools.calculations
    def assess(self,fixture):
        draft,ctx,ledger=fixture
        return assess_draft(draft,ctx,ledger,expected_goal_ref='goal-1')
    def codes(self,assessment):return {f['code'] for f in assessment['findings']+[f for s in assessment['strategies'] for f in s['findings']]}

    def test_comfortable_positive_cash_passes_without_activation_or_recalculation(self):
        fixture=self.make();before=deepcopy(fixture)
        with patch.object(PlanningTools,'calculate_strategy',side_effect=AssertionError('No calculation replay')):
            a=self.assess(fixture);again=self.assess(fixture)
        self.assertEqual(a,again);self.assertEqual(fixture,before)
        self.assertEqual(a['status'],'PASS');self.assertEqual(a['recommended_status'],'PASS')
        self.assertFalse(a['activation_allowed']);self.assertFalse(a['actionable'])
        self.assertEqual(a['strategies'][0]['evidence']['cash_after_contribution'],1400)

    def test_above_stretch_and_between_capacities_cannot_pass(self):
        for amount,code in [(1100,'EXCEEDS_STRETCH_CAPACITY'),(950,'HEADROOM_NOT_ESTABLISHED')]:
            a=self.assess(self.make(amount=amount))
            self.assertEqual(a['status'],'REVISE');self.assertIn(code,self.codes(a))

    def test_vet_paid_critical_regression_independent_of_gemini_claimed_safety(self):
        fixture=self.make('vet_paid');fixture[0]['result']['assessment']='feasible'
        a=self.assess(fixture)
        self.assertEqual(a['status'],'REVISE')
        self.assertIn('EXCEEDS_STRETCH_CAPACITY',self.codes(a));self.assertIn('NEGATIVE_PROJECTED_CASH',self.codes(a))
        evidence=a['strategies'][0]['evidence']
        self.assertEqual(evidence['proposed_monthly'],600)
        self.assertEqual(evidence['stretch_capacity_monthly'],590.73)
        self.assertEqual(evidence['cash_after_contribution'],-450)
        self.assertGreater(evidence['monthly_surplus_after_contribution'],0)

    def test_unpaid_bill_reserves_cash_once_and_paid_is_not_deducted_twice(self):
        unpaid=self.assess(self.make('vet_unpaid'));paid=self.assess(self.make('vet_paid'))
        u=unpaid['strategies'][0]['evidence'];p=paid['strategies'][0]['evidence']
        self.assertEqual((u['current_cash_balance'],u['confirmed_pending_due'],u['cash_after_known_obligations']),(2000,1850,150))
        self.assertEqual((p['current_cash_balance'],p['confirmed_pending_due'],p['cash_after_known_obligations']),(150,0,150))
        self.assertEqual(u['cash_after_contribution'],-450);self.assertEqual(p['cash_after_contribution'],-450)
        self.assertEqual(u['expense_lifecycle'][0]['status'],'outstanding')
        self.assertEqual(p['expense_lifecycle'][0]['status'],'posted')
        self.assertNotEqual(unpaid['status'],'PASS');self.assertNotEqual(paid['status'],'PASS')

    def test_missing_invalid_or_nonfinite_evidence_blocks(self):
        for key in ['one_month_comparison','funding_reserve_known']:
            draft,ctx,ledger=self.make();ledger['calculation-1'][key]=None
            draft['calculations']=deepcopy(ledger)
            self.assertEqual(self.assess((draft,ctx,ledger))['status'],'BLOCK')
        for value in [None,float('nan'),True]:
            draft,ctx,ledger=self.make();ctx['financial_insights']['income']['variability_cv']=value
            self.assertEqual(self.assess((draft,ctx,ledger))['status'],'BLOCK')
        draft,ctx,ledger=self.make();ctx['financial_insights']['cashflow']['runway']=None
        self.assertEqual(self.assess((draft,ctx,ledger))['status'],'BLOCK')
        self.assertEqual(assess_draft({},None,{},expected_goal_ref='goal-1')['status'],'BLOCK')

    def test_unknown_goal_context_and_inconsistent_ledger_block(self):
        for field,value in [('financial_version','stale'),('selected_goal_ref','foreign'),('activation_allowed',True)]:
            draft,ctx,ledger=self.make();draft[field]=value
            self.assertEqual(self.assess((draft,ctx,ledger))['status'],'BLOCK')
        draft,ctx,ledger=self.make();draft['calculations']['calculation-1']['comfortable_capacity']=999999
        self.assertEqual(self.assess((draft,ctx,ledger))['status'],'BLOCK')
        draft,ctx,ledger=self.make();ctx['financial_insights']['balances'][1]['kind']='unknown'
        self.assertEqual(self.assess((draft,ctx,ledger))['status'],'BLOCK')

    def test_multiple_goals_total_exceeds_capacity_and_unknown_existing_commitments(self):
        ctx=deepcopy(self.contexts['normal']);ctx['goals'].append({**ctx['goals'][0],'goal_ref':'goal-2'})
        plans=[{'goal_ref':ref,'contribution':600} for ref in ['goal-1','goal-2']]
        a=self.assess(self.make(context=ctx,plans=plans))
        self.assertEqual(a['status'],'BLOCK')
        self.assertIn('EXCEEDS_STRETCH_CAPACITY',self.codes(a))
        self.assertIn('UNKNOWN_EXISTING_GOAL_COMMITMENTS',self.codes(a))
        self.assertEqual(a['strategies'][0]['evidence']['proposed_monthly'],1200)

    def test_recorded_goal_allocation_requires_designated_savings_backing(self):
        fixture=self.make()
        fixture[1]['financial_insights']['balances'][1]['goal_available']=0
        a=self.assess(fixture)
        self.assertEqual(a['status'],'BLOCK')
        self.assertIn('GOAL_ALLOCATION_NOT_BACKED',self.codes(a))

    def test_later_calculated_deadline_may_pass_but_insufficient_extension_needs_revision(self):
        a=self.assess(self.make(plans=[{'goal_ref':'goal-1','contribution':450,'deadline':'2027-08-31'}]))
        self.assertEqual(a['status'],'PASS')
        a=self.assess(self.make(plans=[{'goal_ref':'goal-1','contribution':450,'deadline':'2027-06-30'}]))
        self.assertEqual(a['status'],'REVISE');self.assertIn('MISSES_EVALUATED_DEADLINE',self.codes(a))
        fixture=self.make(plans=[{'goal_ref':'goal-1','contribution':450,'deadline':'2027-08-31'}])
        fixture[1]['preferences']['allow_deadline_extension']=False
        self.assertEqual(self.assess(fixture)['status'],'BLOCK')

    def test_false_feasibility_or_unsupported_actions_block(self):
        fixture=self.make(amount=450);fixture[0]['result']['strategies'][0]['rationale']='This meets the deadline.'
        self.assertEqual(self.assess(fixture)['status'],'BLOCK')
        fixture=self.make();fixture[0]['result']['strategies'][0]['proposed_actions']=['Activate this goal now.']
        self.assertEqual(self.assess(fixture)['status'],'BLOCK')

    def test_essential_or_speculative_funding_dependency_blocks(self):
        for action,code in [('Reduce groceries to fund savings.','ESSENTIAL_SPENDING_DEPENDENCY'),
                            ('Cancel rent payments.','ESSENTIAL_SPENDING_DEPENDENCY'),
                            ('Use protected reserves to fund the goal.','UNVERIFIED_FUNDING'),
                            ('Assume extra income will fund savings.','UNVERIFIED_FUNDING')]:
            fixture=self.make();fixture[0]['result']['strategies'][0]['proposed_actions']=[action]
            a=self.assess(fixture);self.assertEqual(a['status'],'BLOCK');self.assertIn(code,self.codes(a))
        fixture=self.make();fixture[0]['result']['strategies'][0]['proposed_actions']=['Reduce groceries without touching protected reserves.']
        self.assertIn('ESSENTIAL_SPENDING_DEPENDENCY',self.codes(self.assess(fixture)))
        fixture=self.make();fixture[0]['result']['strategies'][0]['proposed_actions']=['Do not use protected reserves.']
        self.assertEqual(self.assess(fixture)['status'],'PASS')

    def test_outside_window_expense_needs_review_without_invented_debit(self):
        fixture=self.make()
        fixture[1]['shocks']=[{'event_ref':'event-future','status':'anticipated','event_amount':1850,'date':AS_OF.isoformat(),
                             'impact':{'already_in_balance':False,'already_in_projection':False}}]
        a=self.assess(fixture)
        self.assertEqual(a['status'],'REVISE')
        self.assertIn('OBLIGATION_NOT_COVERED_BY_PROJECTION',self.codes(a))
        self.assertEqual(a['strategies'][0]['evidence']['cash_after_contribution'],1400)

    def test_income_variability_monthly_zero_headroom_and_frequency(self):
        fixture=self.make();fixture[1]['financial_insights']['income']['variability_cv']=.5
        self.assertIn('VARIABLE_INCOME_HEADROOM',self.codes(self.assess(fixture)))
        self.assertIn('NO_MONTHLY_HEADROOM',self.codes(self.assess(self.make(amount=900))))
        fixture=self.make(plans=[{'goal_ref':'goal-1','contribution':500,'frequency':'fortnightly'}])
        a=self.assess(fixture)
        self.assertIn('EXCEEDS_STRETCH_CAPACITY',self.codes(a))
        self.assertEqual(a['strategies'][0]['evidence']['proposed_monthly'],1083.33)

    def test_safe_alternative_does_not_hide_unsafe_recommendation(self):
        draft,ctx,ledger=self.make(amount=1100);tools=PlanningTools(ctx);tools.calculations=ledger
        tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
        draft['calculations']=deepcopy(ledger);draft['result']=output(('calculation-1','calculation-2'))
        a=self.assess((draft,ctx,ledger))
        self.assertEqual(a['status'],'REVISE');self.assertEqual(a['recommended_status'],'REVISE')
        self.assertEqual(a['strategies'][1]['status'],'PASS')
        feedback=a['revision_feedback']['strategies'][0]
        self.assertEqual(feedback['calculation_ref'],'calculation-1')
        self.assertEqual(feedback['findings'][0]['evidence']['proposed_monthly'],1100)
        self.assertFalse(a['automatic_revision_supported'])
        self.assertEqual(a['safety_revision_attempts'],0)


class SafetyPersistenceTests(unittest.TestCase):
    def setUp(self):
        from tests.test_gemini_planner import PersistenceTests
        PersistenceTests.setUp(self)
    def ask(self,**kwargs):
        from tests.test_gemini_planner import PersistenceTests
        return PersistenceTests.ask(self,**kwargs)

    def test_persisted_block_has_no_activation_and_manual_approval_still_works(self):
        before=self.store.read(self.customer,AS_OF)
        draft=self.ask()
        self.assertEqual(draft['safety_assessment']['status'],'BLOCK') # Fixture has no transaction/income history.
        self.assertFalse(draft['activation_allowed'])
        self.assertEqual(self.planner.draft(self.bank,self.customer,draft['draft_id'])['safety_assessment'],draft['safety_assessment'])
        self.assertEqual(self.store.read(self.customer,AS_OF),before)
        with self.assertRaises(ValueError):self.bank.decide_proposal(self.customer,draft['draft_id'],'approve',confirmed=True,expected_revision=1)
        with self.assertRaises(ValueError):self.planner.draft(self.bank,'foreign',draft['draft_id'])
        proposal=self.bank.propose_goal_change(self.customer,'goal-id',{'target':5000},expected_revision=1,source='user')
        self.bank.decide_proposal(self.customer,proposal['proposal_id'],'approve',confirmed=True,expected_revision=2)
        self.assertEqual(self.store.read(self.customer,AS_OF)[1]['goals']['goal-id']['target'],5000)

    def test_follow_up_transmits_feedback_and_changed_parameters_use_new_calculation(self):
        from tests.test_gemini_corrections import Scripted
        first=self.ask();self.mock=Scripted([output()],amount=400);self.planner.transport=self.mock
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calculate:
            second=self.ask(request_id='follow-up',session_id=first['session_id'],question='Revise the contribution with fresh calculations.')
        self.assertEqual(calculate.call_count,1)
        self.assertEqual(first['calculations']['calculation-1']['plans'][0]['contribution'],600)
        self.assertEqual(second['calculations']['calculation-1']['plans'][0]['contribution'],400)
        payload=json.loads(self.mock.calls[0][0][0].parts[0].text)
        self.assertEqual(payload['recent_conversation'][0]['independent_safety_feedback'],first['safety_assessment']['revision_feedback'])
        self.assertTrue(self.mock.calls[0][1].tools)
        self.assertFalse(second['activation_allowed'])

    def test_stale_context_during_safety_check_prevents_persistence(self):
        from planning.schemas import AccountSnapshot
        import planning.planner_service as service
        original=service.assess_draft
        def changed(*args,**kwargs):
            result=original(*args,**kwargs)
            self.raw.update(accounts=(AccountSnapshot('cash','transaction',150,AS_OF),))
            return result
        with patch.object(service,'assess_draft',side_effect=changed),self.assertRaises(StaleState):self.ask()
        row=self.planner.store.history(self.customer)[0]
        self.assertEqual(row['status'],'stale');self.assertNotIn('draft',row)
