"""Explicit financial-claim regression tests. All provider outputs here are test doubles."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from planning.agent import GeminiPlanner
from planning.agent_tools import PlanningTools
from planning.financial_facts import financial_facts
from planning.proposal_validation import validate_proposal,ProposalValidationError,Category
from tests.test_gemini_corrections import Scripted,proposal
from tests.test_gemini_planner import output
from planning.demo import demo_contexts,AS_OF


class FinancialNarrativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.contexts=demo_contexts()
    def evidence(self,case='vet_unpaid',amount=600):
        ctx=self.contexts[case];tools=PlanningTools(ctx)
        tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':amount}]})
        return ctx,tools.calculations
    def validate(self,text,case='vet_unpaid',amount=600,field='rationale'):
        ctx,ledger=self.evidence(case,amount);obj=output()
        obj['strategies'][0][field]=text
        return validate_proposal(json.dumps(obj),ledger,ctx)
    def rejected(self,text,**kwargs):
        with self.assertRaises(ProposalValidationError) as cm:self.validate(text,**kwargs)
        self.assertTrue(cm.exception.recoverable)
        self.assertIn(Category.NARRATIVE,[i.error_type for i in cm.exception.issues])
        return cm.exception

    def test_facts_preserve_sources_values_units_periods_and_unknowns(self):
        ctx,ledger=self.evidence();before=deepcopy((ctx,ledger));facts=financial_facts(ctx,ledger)
        by={f['metric']:f for f in facts['context']}
        self.assertEqual(by['current_cash_balance']['value'],2000)
        self.assertEqual(by['cash_after_known_obligations']['value'],150)
        self.assertEqual(by['confirmed_pending_due']['value'],1850)
        rows={f['metric']:f for f in facts['calculations']['calculation-1']['facts']}
        self.assertEqual(rows['cash_after_contribution']['value'],-450)
        self.assertEqual(rows['monthly_surplus_after_contribution']['value'],300)
        self.assertEqual(rows['comfortable_capacity']['period']['months'],[r['month'] for r in ctx['financial_insights']['cashflow']['monthly']])
        for f in list(by.values())+list(rows.values()):
            for k in ['value','unit','period','meaning','source','calculation_ref','assumptions']:self.assertIn(k,f)
        self.assertEqual(rows['cash_after_contribution']['calculation_ref'],'calculation-1')
        self.assertIn('not a full future balance forecast',rows['cash_after_contribution']['period']['basis'])
        self.assertEqual((ctx,ledger),before)
        unknown=deepcopy(ctx);unknown['financial_insights']['cashflow']['runway']=None
        tool=PlanningTools(unknown);tool.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
        missing=financial_facts(unknown,tool.calculations)
        self.assertNotIn('current_cash_balance',[f['metric'] for f in missing['context']])
        self.assertNotIn('cash_after_contribution',[f['metric'] for f in missing['calculations']['calculation-1']['facts']])

    def test_positive_cash_when_negative_and_negative_when_positive(self):
        for text in ['You will still have positive cash available.','Preserving positive one-month liquidity.',
                     'The projected cash balance remains positive.','The one-month cash balance is zero.']:
            self.rejected(text)
        self.rejected('The projected cash balance is negative.',case='normal')
        self.validate('The projected cash balance is negative.')
        self.validate('The projected cash balance is −$450.')
        self.validate('The projected cash balance is AUD -450.')
        self.validate('The projected cash balance is positive.',case='normal')

    def test_zero_monthly_flow_is_not_zero_cash_or_no_shortfall(self):
        self.rejected('Leaves zero immediate one-month cash residual after obligations and contributions in the upcoming month.',amount=900)
        self.rejected('There is no cash shortfall.',amount=900)
        self.validate('The monthly surplus after contribution is AUD 0. The projected cash balance is negative.',amount=900)

    def test_current_and_projected_balances_are_not_interchangeable(self):
        self.validate('The current balance is AUD 2000. The projected cash after known obligations is AUD 150.')
        self.validate('The current balance is positive, but the projected cash balance is negative.')
        self.rejected('The current balance is AUD 150.')
        self.rejected('The projected cash balance is AUD 2000.')

    def test_distinct_periods_conditionals_and_negation_do_not_false_positive(self):
        for text in ['After the next payday cash could be positive.',
                     'If income changes, projected cash might be positive.',
                     'Current cash is positive; projected cash is negative.',
                     'This does not leave positive one-month liquidity.',
                     'The historical monthly surplus is AUD 900. The monthly surplus after contribution is AUD 300.']:
            self.validate(text)

    def test_explicit_cash_affordability_is_not_capacity(self):
        for text in ['The contribution is affordable from current cash.','Current cash covers the contribution.','There is no projected cash shortfall.']:
            self.rejected(text)
        self.validate('The schedule meets the deadline, but projected cash is negative; affordability remains uncertain.')

    def test_paid_unpaid_and_double_debit_claims(self):
        self.rejected('The bill is already paid.')
        self.rejected('The bill is still unpaid.',case='vet_paid',amount=450)
        self.rejected('Deduct the bill again from the projection.')
        self.rejected('Deduct the expense again.',case='vet_paid',amount=450)
        self.rejected('The bill is not included in the projection.')
        self.validate('The bill remains outstanding and is included in the projection.')
        self.validate('The bill is not already paid.')
        self.validate('The expense is already paid; do not deduct it again.',case='vet_paid',amount=450)

    def test_named_deadline_dates_and_relationships(self):
        self.rejected('Meets the requested deadline on May 30, 2027.',case='normal')
        self.rejected('The completion date is 31 May 2027.',case='normal')
        self.rejected('Completes on the deadline.',case='normal')
        self.validate('Completion is 30 May 2027, before the requested deadline 31 May 2027.',case='normal')
        self.validate('Completes before the deadline.',case='normal')
        self.rejected('Completion date is 2027-07-30, before the requested deadline 2027-05-31.',case='normal',amount=500)
        self.rejected('Completion on July 30, 2027, before the deadline May 31, 2027.',case='normal',amount=500)
        self.validate('Completion date is 2027-07-30, after the requested deadline 2027-05-31.',case='normal',amount=500)
        self.validate('The expense was recorded on 2026-09-30, before the deadline 2027-05-31.',case='normal',amount=500)

    def test_wrong_contribution_still_fails_and_no_uncalculated_schedule(self):
        with self.assertRaises(ProposalValidationError):self.validate('Contribute AUD 500 monthly.')
        ctx,ledger=self.evidence();obj=output();obj['strategies'][0]['contribution']=500
        with self.assertRaises(ProposalValidationError) as cm:validate_proposal(json.dumps(obj),ledger,ctx)
        self.assertFalse(cm.exception.recoverable)
        for text in ['Contribute AUD 600 fortnightly.','This is within comfortable capacity.','This is within stretch capacity.']:
            with self.assertRaises(ProposalValidationError):self.validate(text,case='vet_paid')

    def test_mixed_strategy_global_feasibility_is_scoped(self):
        ctx,ledger=self.evidence('normal',500);tools=PlanningTools(ctx);tools.calculations=ledger
        tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
        obj=output(('calculation-1','calculation-2'))
        obj['answer']='Calculation-2 meets the deadline. Calculation-1 completes after the deadline.'
        validate_proposal(json.dumps(obj),ledger,ctx)
        obj['answer']='Both strategies meet the deadline.'
        with self.assertRaises(ProposalValidationError):validate_proposal(json.dumps(obj),ledger,ctx)

    def test_feedback_precise_correction_reuses_calculations_and_facts(self):
        ctx=self.contexts['vet_paid'];bad=proposal(450);bad['strategies'][0]['rationale']='Preserving positive one-month liquidity.'
        good=deepcopy(bad);good['strategies'][0]['rationale']='The monthly surplus after contribution is AUD 244.44, but the projected cash balance is negative.'
        t=Scripted([bad,good],amount=450);agent=GeminiPlanner(t,final_output_mode='json_text')
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,side_effect=PlanningTools.calculate_strategy) as calc:
            draft=agent.run(ctx,goal_ref='goal-1',question='Assess')
        self.assertEqual(calc.call_count,1)
        feedback=json.loads(t.calls[-1][0][-1].parts[0].text)
        issue=next(i for i in feedback['validation_errors'] if i['error_type']=='financial_narrative_mismatch')
        self.assertEqual(issue['field'],'strategies[0].rationale')
        self.assertEqual(issue['authoritative_values']['facts'][0]['value'],-300)
        self.assertEqual(issue['authoritative_values']['facts'][0]['calculation_ref'],'calculation-1')
        self.assertEqual(feedback['authoritative_python_calculations'],draft['baseline_calculations'])
        self.assertIn('authoritative_financial_facts',feedback)
        self.assertTrue(draft['metadata']['correction_succeeded'])
        self.assertEqual(draft['metadata']['correction_calculation_calls'],0)
        self.assertEqual(draft['status'],'draft_unverified');self.assertFalse(draft['activation_allowed'])

    def test_failed_correction_stops_and_new_claim_is_revalidated(self):
        bad=proposal(600);bad['strategies'][0]['rationale']='The projected cash balance is positive.'
        t=Scripted([bad]*3);agent=GeminiPlanner(t,final_output_mode='json_text')
        with self.assertRaises(ProposalValidationError):agent.run(self.contexts['vet_unpaid'],goal_ref='goal-1',question='Assess')
        self.assertEqual(agent.last_metrics['correction_attempts'],2)
        self.assertFalse(agent.last_metrics['success'])
        edited=deepcopy(bad);edited['strategies'][0]['rationale']='The projected cash balance is negative.';edited['strategies'][0]['contribution']=500
        t=Scripted([bad,edited]);agent=GeminiPlanner(t)
        with self.assertRaises(ProposalValidationError):agent.run(self.contexts['vet_unpaid'],goal_ref='goal-1',question='Assess')
        self.assertTrue(agent.last_metrics['new_calculations_required'])
