"""Bounded orchestration helpers; no recommendations or financial arithmetic."""
from .agent_models import CalculationRequest
from .agent_tools import CalculationInputError
from .gemini import bounded_setting


def safety_revision_limit():
    return bounded_setting('GEMINI_MAX_SAFETY_REVISIONS',1,0,1)


def signature(arguments,context):
    request=CalculationRequest.model_validate(arguments)
    goals={g['goal_ref']:g for g in context['goals']}
    rows=[]
    for p in request.plans:
        if p.goal_ref not in goals:
            raise CalculationInputError('plans','goal_scope','Use only authorised goal references.')
        g=goals[p.goal_ref]
        rows.append((p.goal_ref,p.contribution,p.frequency,p.pause_months,
                     g['target'] if p.target is None else p.target,
                     g['deadline'] if p.deadline is None else p.deadline.isoformat(),p.priority))
    return (tuple(sorted(rows)),request.hypothetical_one_off_expense,request.hypothetical_missing_income)


def completed_signature(calc):
    return (tuple(sorted((p['goal_ref'],p['contribution'],p['frequency'],p['pause_months'],p['target'],p['deadline'],p['priority']) for p in calc['plans'])),
            calc['hypothetical_additional_expense'],calc['hypothetical_additional_missing_income'])


def safety_feedback(proposal,assessment):
    return {'task':'Revise the financial proposal in response to the independent checker, not merely its wording.',
        'original_proposal_source':'Immediately preceding assistant response; use its calculation refs and unchanged prior Python results.','overall_status':assessment['status'],'recommended_ref':assessment['recommended_ref'],
        'global_findings':assessment['findings'],
        'strategies':[{'calculation_ref':s['calculation_ref'],
            'role':'recommended' if s['calculation_ref']==assessment['recommended_ref'] else 'alternative',
            'status':s['status'],'findings':s['findings'],'authoritative_evidence_source':'Prior Python result '+s['calculation_ref']}
            for s in assessment['strategies']],
        'instructions':'Preserve strategies that passed where appropriate. Develop genuine financial changes for the failing strategies using their actual amounts, units, dates and comparison periods. Do not relabel an unsafe calculation as safe or just soften its explanation. Call calculate_strategy for every changed contribution, frequency, pause, target, deadline or priority. Unchanged financial options may reuse existing calculation refs; unnecessary unsafe alternatives may be omitted if justified. The checker will evaluate the complete proposal again independently. Never invent funding, spend protected reserves, omit obligations or claim to override the checker. Do not produce final JSON yet: use calculation tools, or finish_planning if retaining only unchanged calculated options.'}


def revision_eligible(assessment):
    """A rewrite never approves a blocked option or hides a shared source failure."""
    if assessment['status']=='REVISE':return True
    if assessment['status']!='BLOCK' or assessment.get('recommended_status')!='PASS' or assessment.get('findings'):return False
    allowed={'INCOMPLETE_OPTION_SCHEDULE','UNKNOWN_COMPLETION','MISSES_EVALUATED_DEADLINE',
        'NEGATIVE_DATED_CASH','LOW_DATED_HEADROOM','EXCEEDS_STRETCH_CAPACITY','HEADROOM_NOT_ESTABLISHED',
        'NO_MONTHLY_HEADROOM','VARIABLE_INCOME_HEADROOM','RELIABLE_FUNDING_INCOME_UNAVAILABLE'}
    failures=[f['code'] for row in assessment['strategies'] if row['status']!='PASS' for f in row['findings'] if f['outcome']!='PASS']
    return bool(set(failures)&{'INCOMPLETE_OPTION_SCHEDULE','UNKNOWN_COMPLETION','MISSES_EVALUATED_DEADLINE'}) and set(failures)<=allowed
