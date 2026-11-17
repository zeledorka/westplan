"""Surplus terminology regressions use deterministic, labelled model test doubles."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from planning.agent import GeminiPlanner
from planning.agent_tools import PlanningTools
from planning.proposal_validation import validate_proposal, ProposalValidationError, Category
from planning.demo import demo_contexts
from tests.test_gemini_corrections import Scripted, proposal


class SurplusWordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context=demo_contexts()['normal']

    def tools(self, context=None):
        tools=PlanningTools(context or self.context)
        tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
        return tools

    def test_authoritative_amounts_represent_distinct_concepts(self):
        ctx=deepcopy(self.context)
        # Do not rely on capacity and historical surplus coincidentally being equal.
        ctx['financial_insights']['cashflow']['savings_capacity']['comfortable']=700
        tools=self.tools(ctx);calc=tools.calculations['calculation-1']
        self.assertEqual(ctx['financial_insights']['cashflow']['average_surplus'],900)
        self.assertEqual(calc['comfortable_capacity'],700)
        self.assertEqual(calc['one_month_comparison']['monthly_surplus_after_contribution'],300)
        self.assertEqual(calc['one_month_comparison']['liquid_after_pending_reserve_and_contribution'],1400)

    def test_explicit_historical_average_uses_historical_value_not_residual(self):
        tools=self.tools()
        for qualifier in ['historical','average','historical average']:
            obj=proposal(600)
            obj['answer']=f'The {qualifier} monthly surplus is AUD 900. The monthly surplus after contribution is AUD 300.'
            validate_proposal(json.dumps(obj),tools.calculations,self.context)
            obj['answer']=f'The {qualifier} monthly surplus is AUD 300.'
            with self.assertRaises(ProposalValidationError) as cm:
                validate_proposal(json.dumps(obj),tools.calculations,self.context)
            self.assertEqual(cm.exception.issues[0].error_type,Category.MONEY)

    def test_unqualified_capacity_and_balance_cannot_be_called_remaining_surplus(self):
        tools=self.tools()
        for wording in ['The comfortable monthly surplus is AUD 900.',
                        'The remaining monthly surplus is AUD 900.',
                        'The remaining monthly surplus is AUD 1400.',
                        'The monthly surplus after contribution is AUD 600.']:
            obj=proposal(600);obj['answer']=wording
            with self.assertRaises(ProposalValidationError) as cm:
                validate_proposal(json.dumps(obj),tools.calculations,self.context)
            issue=cm.exception.issues[0]
            self.assertTrue(cm.exception.recoverable)
            self.assertEqual(issue.error_type,Category.MONEY)
            self.assertEqual(issue.authoritative_values['historical_average_monthly_surplus'],900)
            self.assertEqual(issue.authoritative_values['capacity_estimates']['calculation-1']['comfortable_capacity'],900)
            self.assertEqual(issue.authoritative_values['cashflow']['calculation-1']['monthly_surplus_after_contribution'],300)
            self.assertIn('before the proposed contribution',issue.correction_required)
        # Residual disambiguation must not skip a separate, wrong contribution claim.
        obj=proposal(600)
        obj['answer']='The contribution is AUD 500. The monthly surplus after contribution is AUD 300.'
        with self.assertRaises(ProposalValidationError) as cm:
            validate_proposal(json.dumps(obj),tools.calculations,self.context)
        self.assertIn('contribution amount/frequency',cm.exception.issues[0].correction_required)

    def test_each_strategy_residual_must_match_its_own_calculation(self):
        tools=self.tools()
        tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':900}]})
        from tests.test_gemini_planner import output
        obj=output(('calculation-1','calculation-2'))
        obj['strategies'][0]['rationale']='The remaining monthly surplus is AUD 300.'
        obj['strategies'][1]['rationale']='The remaining monthly surplus is AUD 0.'
        validate_proposal(json.dumps(obj),tools.calculations,self.context)
        obj['strategies'][1]['rationale']='The remaining monthly surplus is AUD 300.'
        with self.assertRaises(ProposalValidationError) as cm:
            validate_proposal(json.dumps(obj),tools.calculations,self.context)
        self.assertEqual(cm.exception.issues[0].calculation_ref,'calculation-2')

    def test_field_then_surplus_correction_preserves_ledger_and_draft_restrictions(self):
        ambiguous=proposal(600);ambiguous['answer']='The comfortable monthly surplus is AUD 900.'
        corrected=deepcopy(ambiguous)
        corrected['answer']='The historical average monthly surplus is AUD 900. The monthly surplus after contribution is AUD 300.'
        transport=Scripted([{**ambiguous,'change_requested':False},ambiguous,corrected])
        agent=GeminiPlanner(transport,final_output_mode='json_text')
        with patch.object(PlanningTools,'calculate_strategy',autospec=True,
                          side_effect=PlanningTools.calculate_strategy) as calc:
            draft=agent.run(self.context,goal_ref='goal-1',question='Assess')
        self.assertEqual(calc.call_count,1)
        self.assertEqual(draft['metadata']['correction_attempts'],2)
        self.assertEqual(draft['metadata']['correction_calculation_calls'],0)
        feedback=json.loads(transport.calls[-1][0][-1].parts[0].text)
        self.assertEqual(feedback['authoritative_python_calculations'],draft['baseline_calculations'])
        self.assertEqual(feedback['validation_errors'][0]['authoritative_values']['historical_average_monthly_surplus'],900)
        self.assertEqual(draft['result'],corrected)
        self.assertEqual(draft['status'],'draft_unverified')
        self.assertFalse(draft['activation_allowed'])
        for _,config,_ in transport.calls[1:]:
            self.assertEqual(config.tool_config.function_calling_config.mode,'NONE')
