"""Deterministic goal admission using the planner's Python ledger and safety checker."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
from .agent_tools import PlanningTools
from .safety import assess_draft, finding


class GoalSafetyError(ValueError):
    def __init__(self, assessment):
        self.assessment = assessment
        super().__init__('Goal not approved: ' + assessment['status'] + '. Change the target or deadline, or resolve the reported financial evidence.')


def commitments_version(context):
    """Assessment freshness excludes its own provenance, never financial facts or schedules."""
    data = {k: v for k, v in context.items() if k not in {'state_version', 'goal_versions', 'goal_assessments'}}
    return sha256(json.dumps(data, sort_keys=True, allow_nan=False).encode()).hexdigest()


def assess_goal(context, goal_ref, *, contribution=None, frequency='monthly', pause_months=0):
    context = deepcopy(context)
    if contribution is None:
        # A derived replacement schedule must use the same new anchor in both
        # required-amount derivation and final evaluation. Other goals retain cadence.
        context['recorded_goal_schedules']=[s for s in context.get('recorded_goal_schedules',[]) if s['goal_ref']!=goal_ref]
    tools = PlanningTools(context)
    schedules = {s['goal_ref']: s for s in context.get('recorded_goal_schedules', [])}
    plans = []
    unknown = False
    unsupported_pause = False
    for goal in context['goals']:
        ref = goal['goal_ref']
        if ref == goal_ref:
            plans.append(dict(goal_ref=ref, contribution=0.0 if contribution is None else contribution,
                              frequency=frequency, pause_months=pause_months))
        elif ref in schedules:
            # Do not silently restart an existing pause relative to today's date.
            # Dated paused commitments are explicitly unsupported below.
            s = schedules[ref]
            unsupported_pause = unsupported_pause or bool(s.get('pause_months', 0))
            plans.append(dict(goal_ref=ref, contribution=s.get('contribution'),
                              frequency=s.get('frequency'), pause_months=0))
        else:
            unknown = True
            plans.append(dict(goal_ref=ref, contribution=0.0))
    try:
        if unsupported_pause:
            raise ValueError('Existing paused schedules require a dated combined assessment')
        preliminary = tools.calculate_strategy({'plans': plans})
        selected = next(row for row in preliminary['plans'] if row['goal_ref'] == goal_ref)
        if contribution is None:
            required = selected['required_per_period']
            if required is None:
                raise ValueError('No contribution dates before the requested deadline')
            next(plan for plan in plans if plan['goal_ref'] == goal_ref)['contribution'] = required
        # Only the final calculation is evidence; preliminary zero contributions never
        # become an approved schedule or influence the safety decision.
        tools = PlanningTools(context)
        calculation = tools.calculate_strategy({'plans': plans})
        selected = next(row for row in calculation['plans'] if row['goal_ref'] == goal_ref)
        context['recorded_goal_schedules'] = [{**plan,'schedule_as_of':next(row['schedule_as_of'] for row in calculation['plans'] if row['goal_ref']==plan['goal_ref'])} for plan in plans if plan['goal_ref'] == goal_ref or plan['goal_ref'] in schedules]
        context['state_version'] = sha256(json.dumps({k:v for k,v in context.items() if k!='state_version'}, sort_keys=True, allow_nan=False).encode()).hexdigest()
        result = dict(objective='Assess the requested goal schedule.', answer='Evaluate the authoritative calculated schedule.',
                      assessment='constrained', change_recommended=False, recommended_ref=calculation['calculation_ref'],
                      strategies=[dict(calculation_ref=calculation['calculation_ref'], title='Requested schedule',
                                       approach='Use the calculated regular contributions.', proposed_actions=[],
                                       rationale='Evaluate the requested target and deadline using current evidence.',
                                       benefits=[], drawbacks=[], assumptions=[])], assumptions=[], missing_information=[], shock_refs=[])
        draft = dict(status='draft_unverified', activation_allowed=False,
                     financial_version=context['state_version'], selected_goal_ref=goal_ref,
                     result=result, calculations=tools.calculations)
        safety = assess_draft(draft, context, tools.calculations, expected_goal_ref=goal_ref)
        if unknown and safety['status'] == 'PASS':
            raise ValueError('Existing commitments are unknown')
        return dict(status=safety['status'], safety_assessment=safety, calculation=calculation,
                    calculation_ref=calculation['calculation_ref'], schedule=selected,
                    financial_version=context['state_version'], financial_context=context,
                    assessed_at=datetime.now(timezone.utc).isoformat(), assumptions=calculation['assumptions'])
    except (ValueError, TypeError, KeyError, OverflowError):
        return dict(status='BLOCK', safety_assessment=dict(status='BLOCK', strategies=[], findings=[
            finding('UNASSESSABLE_GOAL_SCHEDULE', 'BLOCK',
                    'Cannot establish a valid schedule from current evidence. Check the deadline (within ten years), contribution frequency and existing commitments. Existing paused commitments require a dated combined assessment before adding or changing another goal.')]),
                    financial_version=context.get('state_version'), assessed_at=datetime.now(timezone.utc).isoformat())
