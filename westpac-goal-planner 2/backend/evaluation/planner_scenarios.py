"""Planning-quality cases, not fixed strategies. All ledgers are synthetic."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
import json
from planning.demo import demo_contexts
from planning.context import build_planning_context
from planning.schemas import Goal
from .shock_scenarios import scenarios as shock_scenarios


def cases():
    demo=demo_contexts()
    result=[]
    def add(name,context,question,expectation,tags):
        result.append({'name':name,'context':context,'question':question,'expectation':expectation,'tags':tags})
    add('normal',demo['normal'],'Can I achieve this holiday goal without using protected emergency savings? Compare another approach only if useful.',
        'Recognise that the required contribution is within historical capacity, keep protected funds unavailable, and avoid forcing a goal change. The actual current contribution is not recorded.', ['feasible','protected'])
    add('vet_paid',demo['vet_paid'],'How do the latest financial facts affect this goal? What approach would you recommend?',
        'Recognise already-paid expense, current cash 150 and capacity 490.73; do not debit 1850 again. Address current liquidity and constrained contributions.', ['low_cash','paid'])
    add('vet_unpaid',demo['vet_unpaid'],'How do the latest financial facts affect this goal? What approach would you recommend?',
        'Distinguish cash 2000 from committed 1850, projected 150, and historical capacity 900. Do not treat the unpaid bill as already posted or spend committed funds.', ['low_cash','outstanding'])
    for source in shock_scenarios():
        if source['name'] not in {'missing_salary','reduced_salary','delayed_salary','planned_holiday','irregular_freelance',
                'persistent_essential_increase','affordable_unusual_purchase','cash_straining_purchase'}: continue
        context=build_planning_context(source['txns'],as_of=source['as_of'],goals=(Goal('quality-holiday','holiday',4000,0,date(2027,5,31)),),**source['kwargs'])
        add(source['name'],context,'Assess this goal using the supplied income and event evidence. What uncertainties and alternatives matter?',source['reason'],[source['name']])
    multi=deepcopy(demo['normal'])
    multi['goals'].append({'goal_ref':'goal-2','kind':'emergency_fund','target':3000,'allocated':0,'deadline':'2027-05-31'})
    multi['state_version']=sha256(json.dumps(multi,sort_keys=True).encode()).hexdigest()
    add('multiple_goals',multi,'Consider my emergency fund as a higher priority than my holiday. Compare combined contributions without increasing the available capacity.',
        'Account for both goals, competing contributions and explicit priority; do not invent additional capacity or reallocate protected savings.', ['multiple'])
    short=deepcopy(demo['normal']);short['goals'][0]['deadline']='2026-10-02'
    short['state_version']=sha256(json.dumps(short,sort_keys=True).encode()).hexdigest()
    add('unrealistic_deadline',short,'Can I reach this goal by the requested deadline?',
        'Identify the lack of contribution periods and unreached target. Do not invent a lump sum; clarify or discuss a later date.', ['unachievable'])
    add('no_material_change',demo['normal'],'Hypothetically I contribute $600 each month. No financial circumstances changed. Is a change justified?',
        'Consider maintaining the feasible 600 monthly hypothesis; no change is a valid recommendation, without inventing an approved schedule.', ['no_change'])
    add('protected_essentials',demo['vet_paid'],"I do not want to reduce groceries, health expenses or other essentials. What alternatives remain?",
        'Respect the refusal to cut essentials, discuss timing or contributions and uncertainty using the same constrained cashflow facts.', ['essentials','low_cash'])
    return result


def review(case,draft):
    """Observable checks, explicitly not an independent financial Safety Checker."""
    options=draft['result']['strategies'];calculations=draft['calculations']
    text=' '.join([draft['result']['answer']]+[o['rationale']+' '+o['approach'] for o in options]).lower()
    tagged=case['tags']
    checks={'draft_only':draft['status']=='draft_unverified' and draft['activation_allowed'] is False,
        'tool_supported_options':all(o['calculation_ref'] in calculations for o in options),
        'all_goals_calculated':all(len(c['plans'])==len(case['context']['goals']) for c in calculations.values()),
        'assumptions_disclosed':bool(draft['result']['assumptions'] or any(o['assumptions'] for o in options)),
        'source_allocations_preserved':all(plan['allocated']==next(g['allocated'] for g in case['context']['goals'] if g['goal_ref']==plan['goal_ref']) for c in calculations.values() for plan in c['plans']),
        'no_unrequested_extra_shock':all(c['hypothetical_additional_expense']==0 and c['hypothetical_additional_missing_income']==0 for c in calculations.values())}
    if 'low_cash' in tagged:
        checks['mentions_liquidity_constraint']=any(w in text for w in ('liquid','buffer','cash','flexibility'))
    if 'unachievable' in tagged:
        checks['recognises_deadline_problem']=draft['result']['assessment'] in {'not_achievable','constrained','needs_clarification'}
    if 'multiple' in tagged:
        checks['competing_goals_present']=all(len(c['plans'])>1 for c in calculations.values())
    signatures={json.dumps(c['plans'],sort_keys=True) for c in calculations.values()}
    checks['alternatives_differ_if_present']=len(options)<2 or len(signatures)==len(options)
    return {'scenario':case['name'],'expectation':case['expectation'],'checks':checks,
            'manual_review_required':['Explanation must agree with computed amounts, dates and payment status.',
                'Review whether strategies fit the customer priorities and do not invent funds.',
                'These checks are evaluation diagnostics, not safety approval or real-world quality scores.']}
