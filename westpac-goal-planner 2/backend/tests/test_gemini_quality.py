"""Bounded recovery and factual date-contract regressions; no live calls."""
import json
from copy import deepcopy
import unittest
from google.genai import types
from pydantic import ValidationError
from planning.agent import GeminiPlanner, validate_deadline_precision
from planning.agent_tools import PlanningTools, calculation_error
from planning.agent_models import PlannerResult
from planning.demo import demo_contexts
from planning.gemini import PlannerError
from test_gemini_planner import MockGemini, output, tool_response

class ToolFeedbackTests(unittest.TestCase):
    def test_strict_arguments_return_safe_specific_bounded_errors(self):
        tools=PlanningTools(demo_contexts()['vet_paid'])
        for plan,field,code in [({'goal_ref':'goal-1','contribution':1,'pause_months':1.5},'plans.0.pause_months','int_type'),
                               ({'goal_ref':'goal-1','contribution':1,'frequency':'weekly'},'plans.0.frequency','literal_error'),
                               ({'goal_ref':'goal-1','contribution':'600'},'plans.0.contribution','float_type'),
                               ({'goal_ref':'goal-1','contribution':1,'private-secret-field':100},'plans.0.unsupported_field','extra_forbidden')]:
            with self.subTest(plan=plan),self.assertRaises(ValidationError) as cm:
                tools.calculate_strategy({'plans':[plan]})
            feedback=calculation_error(cm.exception)
            self.assertTrue(feedback['recoverable'])
            self.assertEqual(feedback['issues'][0]['field'],field)
            self.assertEqual(feedback['issues'][0]['code'],code)
            self.assertLessEqual(len(feedback['issues']),4)
            self.assertNotIn('private-secret-field',json.dumps(feedback))
            self.assertEqual(tools.calculations,{})

    def test_domain_errors_identify_scope_target_and_deadline_without_relaxation(self):
        for plan,code in [({'goal_ref':'foreign','contribution':600},'goal_scope'),
                          ({'goal_ref':'goal-1','contribution':600,'target':0},'below_allocation'),
                          ({'goal_ref':'goal-1','contribution':600,'deadline':'2020-01-01'},'deadline_range')]:
            with self.subTest(code=code):
                tools=PlanningTools(demo_contexts()['vet_paid'])
                with self.assertRaises(ValueError) as cm:tools.calculate_strategy({'plans':[plan]})
                self.assertEqual(calculation_error(cm.exception)['issues'][0]['code'],code)
                self.assertEqual(tools.calculations,{})

    def test_model_can_correct_arguments_without_losing_completed_calculations(self):
        class Recovering(MockGemini):
            def generate(self,contents,config,timeout_ms):
                if not config.tools:return super().generate(contents,config,timeout_ms)
                self.calls.append((deepcopy(contents),config,timeout_ms))
                if len(self.calls)==1:
                    parts=[types.Part.from_function_call(name='calculate_strategy',args={'plans':[{'goal_ref':'goal-1','contribution':600}]}),
                           types.Part.from_function_call(name='calculate_strategy',args={'plans':[{'goal_ref':'goal-1','contribution':400,'pause_months':1.5}]})]
                    parts[0].thought_signature=b'preserve-this-signature'
                    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=parts))])
                feedback=[p.function_response.response for c in contents for p in c.parts if p.function_response][-1]
                assert feedback['issues'][0]['field']=='plans.0.pause_months'
                assert feedback['remaining_tool_rounds']==2
                return tool_response([{'goal_ref':'goal-1','contribution':400,'pause_months':1}])
        transport=Recovering()
        draft=GeminiPlanner(transport,final_output_mode='json_text').run(demo_contexts()['vet_paid'],goal_ref='goal-1',question='Assess')
        self.assertEqual(len(transport.calls),3)
        self.assertEqual(draft['metadata']['calculation_validation_errors'],1)
        self.assertEqual(draft['calculations']['calculation-1']['plans'][0]['contribution'],600)
        self.assertEqual(draft['calculations']['calculation-2']['plans'][0]['pause_months'],1)
        contents,config,_=transport.calls[-1]
        self.assertTrue(any(p.thought_signature==b'preserve-this-signature' for c in contents for p in c.parts))
        self.assertEqual(config.tool_config.function_calling_config.mode,'NONE')
        self.assertIn('FINAL OUTPUT PHASE',config.system_instruction)
        self.assertIn('completion_date',contents[-1].parts[0].text)
        self.assertFalse(draft['activation_allowed'])

    def test_repeated_invalid_calls_remain_bounded(self):
        class Invalid(MockGemini):
            def generate(self,*args):
                self.calls.append(args)
                return tool_response([{'goal_ref':'goal-1','contribution':600,'frequency':'weekly'}],finish=False)
        mock=Invalid();agent=GeminiPlanner(mock)
        with self.assertRaises(PlannerError):agent.run(demo_contexts()['vet_paid'],goal_ref='goal-1',question='Assess')
        self.assertEqual(len(mock.calls),3)
        self.assertEqual(agent.last_metrics['calculation_validation_errors'],3)

class NarrativeContractTests(unittest.TestCase):
    def test_exact_deadline_claims_rejected_when_python_date_is_earlier(self):
        for phrase in ['Reach it exactly by the deadline.','Meets the deadline exactly.','Reach it on the deadline.']:
            with self.subTest(phrase=phrase):
                result=output();result['strategies'][0]['approach']=phrase
                with self.assertRaises(PlannerError) as cm:
                    GeminiPlanner(MockGemini(result=result),final_output_mode='json_text').run(demo_contexts()['normal'],goal_ref='goal-1',question='Assess')
                self.assertIn('inconsistent with Python dates',cm.exception.message)

    def test_by_deadline_is_valid_and_equal_dates_allow_exact_claim(self):
        tools=PlanningTools(demo_contexts()['normal']);tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600}]})
        result=output();result['strategies'][0]['approach']='Complete by the deadline on the Python completion date.'
        validate_deadline_precision(PlannerResult.model_validate(result),tools.calculations)
        result['strategies'][0]['rationale']='Achieves the goal exactly on schedule by the deadline.'
        validate_deadline_precision(PlannerResult.model_validate(result),tools.calculations)
        exact=deepcopy(tools.calculations);exact['calculation-1']['plans'][0]['completion_date']='2027-05-31'
        result['strategies'][0]['approach']='Complete exactly at the deadline.'
        validate_deadline_precision(PlannerResult.model_validate(result),exact)

    def test_final_tool_call_is_not_mistaken_for_a_draft(self):
        class FinalTool(MockGemini):
            def generate(self,contents,config,timeout_ms):
                if not config.tools:
                    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role='model',parts=[types.Part.from_function_call(name='finish_planning',args={})]))])
                return super().generate(contents,config,timeout_ms)
        with self.assertRaises(PlannerError) as cm:
            GeminiPlanner(FinalTool(),final_output_mode='json_text').run(demo_contexts()['normal'],goal_ref='goal-1',question='Assess')
        self.assertIn('tool call during final output',cm.exception.message)
