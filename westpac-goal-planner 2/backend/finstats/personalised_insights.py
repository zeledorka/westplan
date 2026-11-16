"""Deterministic observations; no AI strategy selection or duplicated event alerts."""
from hashlib import sha256
from .contracts import category
from .stats import mean, std


def personalised_insights(financial, *, budgets, calendar, opportunities, shocks=(), feedback=()):
    out = []
    period = {'start': financial['observation']['first'], 'end': financial['as_of']}
    def add(kind, cat, key, values, explanation, severity='info', confidence='moderate', actionable=False):
        out.append({'insight_id':sha256((kind+key).encode()).hexdigest()[:24],
                    'type':kind,'category':category(cat),'observation_period':period.copy(),
                    'values':values,'severity':severity,'confidence':confidence,
                    'explanation':explanation,'actionable':actionable})
    shock_categories = {c for s in shocks for c in s['categories']}
    for c in financial['spending']['categories']:
        change=c['recent_change']
        if change is not None and change >= 50 and change > .2*c['monthly_total'] and c['category'] not in shock_categories:
            add('spending_increase',c['category'],c['category']+financial['as_of'][:7],
                {'monthly_average':c['monthly_total'],'recent_change':change},
                f"{category(c['category'])} spending increased compared with earlier complete months.")
    for b in budgets['categories']:
        if b['status'] in {'over_budget','approaching_limit'}:
            add(b['status'],b['category'],b['category']+budgets['month'],
                {'spent':b['spent'],'monthly_limit':b['monthly_limit'],'remaining':b['remaining']},
                f"{category(b['category'])} spending is {'over' if b['status']=='over_budget' else 'approaching'} its monthly budget.",
                'advisory','higher',True)
    for shock in shocks:
        if shock['status']=='projection' or not shock.get('notification_required',True):
            continue  # Current cash-risk insight below supersedes historical projection alerts.
        bill_scores=[e['score'] for e in shock['evidence'] if e['detector']=='recurring_bill_change' and e['score'] is not None]
        kind=('recurring_bill_increase' if any(score>0 for score in bill_scores) else 'recurring_bill_change') if 'recurring_bill_change' in shock['detectors'] else 'unusual_expense'
        if 'burst' in shock['detectors']:
            kind='security_review'
        income_event=shock.get('event_type') in {'missing_income','delayed_income','reduced_income','persistent_income_reduction','income_volatility_change'}
        if income_event:
            kind='income_interruption'
        elif shock['status'] in {'outstanding','anticipated'}:
            kind='outstanding_obligation'
        add(kind,shock['categories'][0] if len(shock['categories'])==1 else 'Other',shock['event_ref'],
            {'event_amount':shock['event_amount'],'event_date':shock['date'],'detector_count':len(shock['detectors'])},
            shock['explanation'] if income_event or kind=='outstanding_obligation' else 'A recurring payment changed; review the observed amount.' if kind in {'recurring_bill_increase','recurring_bill_change'}
            else 'Unusual purchase activity warrants a security review; fraud is not confirmed.' if kind=='security_review'
            else 'An unusual posted expense was observed; this alone does not establish hardship.',
            'critical' if kind=='security_review' else 'advisory','moderate',True)
        out[-1]['_feedback_aliases']=[sha256((kind+key).encode()).hexdigest()[:24] for key in shock.get('_legacy_event_refs',[])]
    flow=financial['cashflow']
    runway=flow['runway']
    if runway:
        # Calendar obligations replace the recurring estimate for the SAME account/window;
        # one-off obligations have unknown funding account and are conservatively included.
        due=sum(b['expected_amount'] for b in calendar['items'] if b['status']=='upcoming'
                and b['expected_date']<=runway['window_end'] and b['account_id'] in {None,runway['account_id']})
        projected=round(runway['balance']-due,2)
        if projected<runway['buffer']:
            add('upcoming_cashflow_risk','Other','current-cash-risk',
                {'balance':runway['balance'],'obligations':round(due,2),'projected':projected,'buffer':runway['buffer']},
                'Upcoming obligations may leave the spending account below its cash buffer.', 'critical','moderate',True)
    monthly=flow['monthly']
    if len(monthly)>=6:
        older=[m['income'] for m in monthly[:-3]]; recent=[m['income'] for m in monthly[-3:]]
        old_cv=std(older)/mean(older) if mean(older)>0 else 0
        new_cv=std(recent)/mean(recent) if mean(recent)>0 else 0
        if new_cv>old_cv+.1:
            add('income_variability','Income','income-'+financial['as_of'][:7],
                {'earlier_cv':round(old_cv,4),'recent_cv':round(new_cv,4)},'Recent monthly income has become less predictable.')
    # Category review insight is suppressed when an event already explains that category.
    reviewed=set()
    for o in opportunities:
        if o['category'] in reviewed or o['category'] in shock_categories:
            continue
        reviewed.add(o['category'])
        add('savings_review',o['category'],o['category'],
            {'observed_monthly_spend':o['observed_monthly_spend'],'requires_confirmation':True},
            f"Observed discretionary {category(o['category'])} spending offers an area to review; no saving is guaranteed.",
            'info',o['confidence'],True)
    latest={f['insight_id']:(position,f['action']) for position,f in enumerate(feedback)
            if f['action'] in {'useful','not_useful','dismiss'}}
    def visible(insight):
        actions=[latest[key] for key in [insight['insight_id']]+insight.get('_feedback_aliases',[]) if key in latest]
        return insight['severity']=='critical' or not actions or max(actions)[1]=='useful'
    return [{k:v for k,v in insight.items() if not k.startswith('_')} for insight in out if visible(insight)]
