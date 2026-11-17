"""Independent deterministic MVP prudence checks over server-owned calculation evidence.

This module cannot mutate a goal, call Gemini, or grant activation. It never offsets
shortfalls with speculative savings. PASS is conditional on the implemented scope.
"""
import json
import math
import re
from datetime import date
from decimal import Decimal
from pydantic import ValidationError
from .proposal_validation import validate_proposal, ProposalValidationError
from .narrative import clauses

POLICY_VERSION='financial-safety-mvp-1'
RANK={'PASS':0,'REVISE':1,'BLOCK':2}


def number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):
        raise ValueError('Missing or invalid authoritative number')
    return value


def finding(code,outcome,message,evidence=None,severity='high'):
    return {'code':code,'outcome':outcome,'severity':severity,'message':message,'evidence':evidence or {}}


def worst(findings):
    return max((f['outcome'] for f in findings),key=RANK.get,default='PASS')


def assess_draft(draft, context, calculation_ledger, *, expected_goal_ref):
    """Only call with backend context/ledger, never client-supplied financial evidence.

    The service obtains the ledger from the read-only Python tool loop, not Gemini's
    final JSON. No calculations are replayed. Context/version/reference checks and
    evidence completeness checks are additional defence, not client authentication.
    """
    if not isinstance(context,dict):context={}
    result={'policy_version':POLICY_VERSION,'financial_version':context.get('state_version'),
            'status':'BLOCK','recommended_ref':None,'recommended_status':'BLOCK',
            'strategies':[],'findings':[],'activation_allowed':False,'actionable':False,
            'automatic_revision_supported':False,'safety_revision_attempts':0,
            'limitations':[
                'Checks cover recorded goals and source evidence, not undisclosed liabilities or guaranteed future income.',
                'Cash is the existing one-month contribution comparison on the runway projection, not a full future funding forecast.',
                'Current contribution schedules are not recorded; multi-goal commitments cannot be fully verified.',
                'AI activation is disabled; bounded automatic revision is governed by the planner, not the checker.']}
    try:
        return _assess(draft,context,calculation_ledger,expected_goal_ref,result)
    except (KeyError,TypeError,ValueError,OverflowError,AttributeError,ValidationError):
        result['findings'].append(finding('MISSING_OR_INVALID_EVIDENCE','BLOCK',
            'Required backend financial evidence is missing or invalid. This proposal cannot pass financial checks.',severity='critical'))
        for row in result['strategies']:
            row['findings'].append(result['findings'][-1]);row['status']='BLOCK'
        return finish(result)


def finish(result):
    all_findings=result['findings']+[f for row in result['strategies'] for f in row['findings']]
    result['status']=worst(all_findings) if result['strategies'] else 'BLOCK'
    result['recommended_status']=next((r['status'] for r in result['strategies'] if r['calculation_ref']==result['recommended_ref']),'BLOCK')
    result['revision_feedback']={'task':'Reconsider strategies that did not pass independent financial checks.',
        'policy_version':POLICY_VERSION,'overall_status':result['status'],
        'strategies':[{'calculation_ref':s['calculation_ref'],'status':s['status'],'findings':s['findings']} for s in result['strategies'] if s['status']!='PASS'],
        'findings':result['findings'],
        'instructions':'Use only authoritative backend facts. Do not invent income, release protected reserves, omit obligations or assume discretionary reductions will occur. Changed contribution, frequency, pause, target or deadline requires a fresh calculate_strategy result before technical validation and safety rechecking. Completed calculations are reused within supported same-assessment corrections/resumes. An explicit new assessment must calculate every proposed option using current inputs; previous-turn calculation refs are historical, not its new calculation ledger. No approval or activation is granted.',
        'next_step':'Use the bounded planner revision when available, or an explicit follow-up assessment to explore further alternatives.'}
    return result


def _assess(draft,context,ledger,expected_goal_ref,result):
    goals={g['goal_ref']:g for g in context['goals']}
    if draft.get('status')!='draft_unverified' or draft.get('activation_allowed') is not False:
        result['findings'].append(finding('DRAFT_BOUNDARY_MISMATCH','BLOCK','Only non-actionable unverified drafts can enter this MVP checker.'))
        return finish(result)
    if draft.get('financial_version')!=context['state_version'] or draft.get('selected_goal_ref')!=expected_goal_ref or expected_goal_ref not in goals:
        result['findings'].append(finding('CONTEXT_OR_GOAL_MISMATCH','BLOCK','The proposal does not match the authorised current goal and financial context.',severity='critical'))
        return finish(result)
    try:
        proposal=validate_proposal(json.dumps(draft['result'],allow_nan=False),ledger,context)
    except ProposalValidationError as exc:
        result['findings'].append(finding('INVALID_PROPOSAL','BLOCK','The proposal did not pass the required schema, reference or narrative checks.',
            {'validation_categories':sorted({i.error_type.value for i in exc.issues})},'critical'))
        return finish(result)
    result['recommended_ref']=proposal.recommended_ref
    result['strategies']=[{'calculation_ref':s.calculation_ref,'status':'BLOCK','findings':[],'evidence':{}} for s in proposal.strategies]
    if not proposal.strategies:
        result['findings'].append(finding('NO_CALCULATED_STRATEGY','BLOCK','More information and a calculated strategy are required before financial checks can pass.'))
        return finish(result)
    financial=context['financial_insights'];cash=financial['cashflow'];runway=cash['runway']
    comfortable=number(cash['savings_capacity']['comfortable']);stretch=number(cash['savings_capacity']['stretch'])
    income=number(financial['income']['monthly_average']);variability=number(financial['income']['variability_cv'])
    essential=number(financial['spending']['monthly_essential']);number(financial['spending']['monthly_discretionary'])
    number(cash['average_surplus']);months=context['financial_insights']['observation']['complete_months']
    date.fromisoformat(context['as_of']);date.fromisoformat(runway['window_end'])
    current=number(runway['balance']);projected=number(runway['projected_after_known_pending'])
    pending=number(runway['additional_known_pending_due']);recurring=number(runway['recurring_bills_due'])
    if min(comfortable,stretch,income,essential,pending,recurring)<0 or comfortable>stretch or variability<0 or not isinstance(months,int):raise ValueError('Invalid context')
    if months<3:
        result['findings'].append(finding('INSUFFICIENT_OBSERVATION','BLOCK','At least three observed complete-month buckets are required by this MVP.',{'complete_months':months}))
    if len(goals)>1:
        result['findings'].append(finding('UNKNOWN_EXISTING_GOAL_COMMITMENTS','BLOCK','All proposed goals are checked together, but existing contribution schedules are not recorded. Confirm existing commitments before proceeding.',{'recorded_goals':len(goals)}))
    if projected<0:
        result['findings'].append(finding('UNFUNDED_KNOWN_OBLIGATIONS','BLOCK','The source projection is already negative after known obligations, before new savings.',{'projected_after_known_obligations':projected,'confirmed_pending_due':pending,'recurring_due':recurring},'critical'))
    preferences=context['preferences']
    funding=[b for b in financial['balances'] if b['kind']=='transaction']
    if len(funding)!=1 or number(funding[0]['protected'])<0:raise ValueError('Unclear funding reserve')
    if any(b['kind']=='unknown' for b in financial['balances']):
        result['findings'].append(finding('UNKNOWN_ACCOUNT_RESTRICTIONS','BLOCK','Source account kinds or restrictions are unknown. Confirm the funding account before financial checks can pass.'))
    if any(number(b['designation_shortfall'])>0 for b in financial['balances']):
        result['findings'].append(finding('DESIGNATION_SHORTFALL','BLOCK','Source balances do not cover their protected/designated amounts. Reconcile them before financial checks can pass.'))
    allocated=sum((Decimal(str(number(g['allocated']))) for g in goals.values()),Decimal(0))
    designated=sum((Decimal(str(number(b['goal_available']))) for b in financial['balances']),Decimal(0))
    if allocated>designated:
        result['findings'].append(finding('GOAL_ALLOCATION_NOT_BACKED','BLOCK','Recorded goal allocations exceed the source savings explicitly designated for goals. Other protected or unallocated funds cannot be assumed available.',{'allocated_across_goals':float(allocated),'designated_goal_savings':float(designated)},'critical'))
    # Expense lifecycle is read from source evidence. It is not deducted again here.
    expenses=[{'event_ref':e['event_ref'],'status':e['status'],'amount':e['event_amount'],
               'already_in_balance':e['impact']['already_in_balance'],'included_in_projection':e['impact']['already_in_projection']}
              for e in context['shocks'] if e.get('event_amount') is not None and e['status'] in {'posted','outstanding','anticipated'}]
    for expense in expenses:
        number(expense['amount'])
        if expense['status'] in {'outstanding','anticipated'} and expense['included_in_projection'] is not True:
            result['findings'].append(finding('OBLIGATION_NOT_COVERED_BY_PROJECTION','REVISE',
                'A declared unpaid or anticipated expense is outside the evaluated projection. Clarify funding and timing; it has not been treated as already paid or deducted again.',expense,'medium'))
    for option,row in zip(proposal.strategies,result['strategies']):
        ref=option.calculation_ref;calc=ledger[ref];plans=calc['plans'];findings=[]
        row['findings']=findings
        if calc['calculation_ref']!=ref or {p['goal_ref'] for p in plans}!=set(goals) or len(plans)!=len(goals):
            findings.append(finding('GOAL_SCOPE_MISMATCH','BLOCK','Calculation evidence must include every authorised goal exactly once.',severity='critical'))
            row['status']='BLOCK';continue
        if draft['calculations'].get(ref)!=calc:raise ValueError('Draft/authoritative ledger mismatch')
        if calc.get('funding_reserve_known') is not True:raise ValueError('Unknown funding reserve')
        monthly=number(calc['aggregate_monthly_equivalent']);immediate=number(calc['immediate_contribution_monthly_equivalent'])
        if monthly<0 or immediate<0 or calc['comfortable_capacity']!=comfortable or calc['stretch_capacity']!=stretch or calc['current_projected_cash_after_known_obligations']!=projected:
            raise ValueError('Source facts mismatch')
        if round(sum(number(p['monthly_equivalent']) for p in plans),2)!=monthly:raise ValueError('Aggregate mismatch')
        comparison=calc['one_month_comparison'];residual=number(comparison['monthly_surplus_after_contribution']);liquid=number(comparison['liquid_after_pending_reserve_and_contribution'])
        row['evidence']={'currency':context['currency'],'as_of':context['as_of'],'runway_window_end':runway['window_end'],
            'cash_basis':'Existing one-month contribution comparison; not a complete dated future forecast.',
            'proposed_monthly':monthly,'immediate_monthly':immediate,'comfortable_capacity_monthly':comfortable,'stretch_capacity_monthly':stretch,
            'current_cash_balance':current,'cash_after_known_obligations':projected,'cash_after_contribution':liquid,
            'monthly_surplus_after_contribution':residual,'historical_monthly_essential':essential,
            'confirmed_pending_due':pending,'recurring_due':recurring,'expense_lifecycle':expenses,
            'income_variability_cv':variability}
        row['evidence'].update(hypothetical_additional_expense=number(calc['hypothetical_additional_expense']),
                              hypothetical_additional_missing_income=number(calc['hypothetical_additional_missing_income']))
        if monthly>stretch:
            findings.append(finding('EXCEEDS_STRETCH_CAPACITY','REVISE','Proposed aggregate monthly savings exceed the calculated stretch capacity.',{'proposed_monthly':monthly,'stretch_capacity_monthly':stretch}))
        elif monthly>comfortable:
            findings.append(finding('HEADROOM_NOT_ESTABLISHED','REVISE','The contribution is between comfortable and stretch capacity. This MVP cannot establish sufficient headroom.',{'proposed_monthly':monthly,'comfortable_capacity_monthly':comfortable,'stretch_capacity_monthly':stretch},'medium'))
        if liquid<0:
            findings.append(finding('NEGATIVE_PROJECTED_CASH','REVISE','The existing cash comparison is negative after obligations, reserve and proposed savings. Funding/timing needs revision.',{'projected_cash':liquid,'cash_after_known_obligations':projected},'critical'))
        elif immediate>0 and liquid<number(runway['buffer']):
            findings.append(finding('LOW_CASH_HEADROOM','REVISE','The cash comparison is below the source engine buffer estimate. Confirm sufficient flexibility.',{'cash_after_contribution':liquid,'source_buffer_estimate':runway['buffer']},'medium'))
        if residual<=0 and monthly>0:
            findings.append(finding('NO_MONTHLY_HEADROOM','REVISE','The proposed savings leave no positive monthly flow headroom for variation.',{'monthly_surplus_after_contribution':residual},'medium'))
        if variability>.3 and monthly>0:
            findings.append(finding('VARIABLE_INCOME_HEADROOM','REVISE','Observed income variability is high; this MVP cannot establish reliable contribution headroom.',{'income_variability_cv':variability,'mvp_caution_threshold':.3},'medium'))
        for p in plans:
            g=goals[p['goal_ref']]
            if p['allocated']!=g['allocated'] or p['frequency'] not in {'monthly','fortnightly'}:raise ValueError('Unsupported source plan')
            for key in ('contribution','required_per_period','target','allocated','monthly_equivalent'):
                if number(p[key])<0:raise ValueError('Invalid source amount')
            if type(p['meets_deadline']) is not bool:raise ValueError('Unknown feasibility')
            date.fromisoformat(p['deadline'])
            if p['completion_date'] is None:
                findings.append(finding('UNKNOWN_COMPLETION','BLOCK','The calculation cannot establish a completion date for this option.',{'goal_ref':p['goal_ref']}))
            elif not p['meets_deadline']:
                findings.append(finding('MISSES_EVALUATED_DEADLINE','REVISE','The calculated schedule misses its evaluated deadline. A sufficient deadline change requires a matching calculation and explicit later approval.',{'goal_ref':p['goal_ref'],'completion_date':p['completion_date'],'evaluated_deadline':p['deadline'],'required_per_period':p['required_per_period'],'proposed_per_period':p['contribution']}))
            if p['completion_date'] is not None:date.fromisoformat(p['completion_date'])
            if p['deadline']>g['deadline'] and not preferences['allow_deadline_extension']:
                findings.append(finding('DEADLINE_PREFERENCE_CONFLICT','BLOCK','The proposed deadline extension conflicts with the recorded user preference.'))
        protected='|'.join(re.escape(c) for c in preferences['protected_categories'])
        essentials='rent|mortgage|utilities|groceries|essential|debt payment'+('|'+protected if protected else '')
        patterns=[('ESSENTIAL_SPENDING_DEPENDENCY',r'\b(?:cut|reduce|skip|cancel|defer)\b.*?\b(?:'+essentials+r')\b',
                   'This option explicitly depends on reducing or deferring essential obligations. No validated reduction is established.'),
                  ('UNVERIFIED_FUNDING',r'\b(?:assume|rely|use|fund|tap)\w*\b.*?\b(?:bonus|extra income|additional income|external funding|protected reserves|emergency savings)\b',
                   'The option explicitly relies on unverified funds or protected reserves. No authoritative funding calculation supports that assumption.')]
        for text in option.proposed_actions+option.assumptions+proposal.assumptions:
            for statement in clauses(text):
                for code,pattern,message in patterns:
                    match=re.search(pattern,statement,re.I)
                    if not match:continue
                    # Negation must qualify this action, not an unrelated later clause.
                    prefix=statement[max(0,match.start()-32):match.start()]
                    if re.search(r'\b(?:not|never|avoid)\s+(?:\w+\s+){0,2}$',prefix,re.I) or re.search(r'\b(?:not|without)\b',match.group(),re.I):continue
                    findings.append(finding(code,'BLOCK',message,severity='critical'))
        row['status']=worst(findings+result['findings'])
    return finish(result)
