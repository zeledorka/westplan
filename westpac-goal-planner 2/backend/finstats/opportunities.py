"""Evidence for optional spending review, never goal strategies or guaranteed savings."""


def spending_opportunities(categories, bills, protected_categories=()):
    protected = set(protected_categories)
    return [{"category": c["category"], "kind": "discretionary_review",
             "observed_monthly_spend": c["monthly_discretionary"],
             "guaranteed_savings": 0,
             "potentially_reducible_recurring_monthly": round(sum(
                 b["typical_amount"] * 365.25 / 12 / b["period_days"]
                 for b in bills if b["category"] == c["category"] and not b["essential"]
                 and b["next_expected"]), 2),
             "assumption": "Review opportunity only; reducibility and cancellation terms unknown."}
            for c in categories if c["monthly_discretionary"] > 0 and c["category"] not in protected]


def find_savings_opportunities(txns, *, as_of, categories, bills, protected_categories=(), subscription_states=None):
    """Evidence-backed review ranges, never additive promises or selected goal strategies."""
    from hashlib import sha256
    from .savings import monthly_flows
    from .stats import mean, std
    from .bills import recurring_series, subscription_id
    from .shocks import detect_recurring_bill_changes
    protected = set(protected_categories)
    out = spending_opportunities(categories, bills, protected)
    for o in out:
        o.update(opportunity_id=sha256(('category:'+o['category']).encode()).hexdigest()[:20],
                 evidence={'monthly_discretionary': o['observed_monthly_spend']},
                 monthly_savings_range={'minimum':0,'maximum':0},confidence='limited',
                 requires_confirmation=True, essential=False, protected=False,
                 savings_range_basis='No reduction assumed without review.')
    complete = {f.month for f in monthly_flows([t for t in txns if t.date <= as_of])}
    def add(kind, cat, key, evidence, upper, confidence='moderate'):
        if cat in protected:
            return
        out.append({'opportunity_id':sha256((kind+key).encode()).hexdigest()[:20],
                    'kind':kind,'category':cat,'evidence':evidence,
                    'monthly_savings_range':{'minimum':0,'maximum':round(max(0,upper),2)},
                    'confidence':confidence,'requires_confirmation':True,'essential':False,'protected':False,
                    'observed_monthly_spend':round(max(0,upper),2),'guaranteed_savings':0,
                    'potentially_reducible_recurring_monthly':0,
                    'assumption':'Conditional review only; opportunities overlap and must not be added together.',
                    'savings_range_basis':'Observed cost or difference from this customer\'s own lower-spend months.'})
    for c in categories:
        cat = c['category']
        values = [sum(-t.amount for t in txns if t.date <= as_of and t.date.strftime('%Y-%m')==m
                      and t.is_expense and not t.essential and t.category==cat) for m in sorted(complete)]
        if len(values)>=3 and mean(values)>=100:
            if min(values)>0:
                add('consistent_discretionary_spending',cat,cat,
                    {'monthly_average':round(mean(values),2),'months':len(values),'minimum_observed':round(min(values),2)},
                    mean(values)-min(values))
            if std(values) > .2*mean(values):
                add('variable_discretionary_spending',cat,cat,
                    {'monthly_average':round(mean(values),2),'monthly_std':round(std(values),2),
                     'minimum_observed':round(min(values),2),'months':len(values)},mean(values)-min(values))
    subscriptions = []
    for s in recurring_series(txns,as_of):
        sid=subscription_id(s.account_id,s.merchant)
        state=(subscription_states or {}).get(sid,{})
        if state.get('status')=='cancelled' and state.get('effective_on','9999')<=as_of.isoformat():
            continue
        if any(t.essential for t in s.occurrences) or s.category in protected or not s.upcoming(as_of,30):
            continue
        amount=s.typical_amount*365.25/12/s.period_days
        sid=subscription_id(s.account_id,s.merchant)
        add('recurring_discretionary_review',s.category,sid,
            {'typical_payment':s.typical_amount,'period_days':s.period_days,'observations':len(s.occurrences)},amount)
        if s.category=='Subscriptions':
            subscriptions.append(s)
        changes=detect_recurring_bill_changes([],series=[s])
        if changes:
            change=changes[-1]
            delta=change.amount-change.details['previous']
            if delta>0:
                add('recurring_cost_increase',s.category,sid,
                    {'previous_payment':change.details['previous'],'current_payment':change.amount,
                     'change_date':change.date.isoformat(),'period_days':s.period_days},delta*365.25/12/s.period_days)
    if len(subscriptions)>1:
        add('possible_service_overlap','Subscriptions','subscriptions',
            {'distinct_series':len(subscriptions),'overlap_confirmed':False},
            min(s.typical_amount*365.25/12/s.period_days for s in subscriptions),'limited')
    return out
