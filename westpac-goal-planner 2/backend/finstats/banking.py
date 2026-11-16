"""Composed banking intelligence for UI and the planning privacy adapter."""
from .insights import financial_insights
from .bills import bills_calendar
from .budgets import budget_summary
from .opportunities import find_savings_opportunities
from .personalised_insights import personalised_insights
from .shock_context import normalized_shocks


def banking_insights(txns, *, as_of, accounts=(), protected_categories=(), limits=(),
                     pending_expenses=(), subscription_states=None, feedback=(), shocks=None):
    past=[t for t in txns if t.date<=as_of]
    protected=set(protected_categories)|{b.category for b in limits if b.protected}
    facts=financial_insights(past,as_of=as_of,accounts=accounts,protected_categories=protected)
    calendar=bills_calendar(past,as_of=as_of,pending_expenses=pending_expenses,subscription_states=subscription_states)
    budgets=budget_summary(past,as_of=as_of,limits=limits,protected_categories=protected,calendar=calendar)
    from .bills import subscription_id
    from .opportunities import spending_opportunities
    active_bills=[]
    for b in facts['recurring']:
        state=(subscription_states or {}).get(subscription_id(b['account_id'],b['merchant']),{})
        if state.get('status')=='cancelled' and state.get('effective_on','9999')<=as_of.isoformat():
            b['next_expected']=None
            b['upcoming_dates']=[]
        else:
            active_bills.append(b)
    facts['opportunities']=spending_opportunities(facts['spending']['categories'],active_bills,protected)
    opportunities=find_savings_opportunities(past,as_of=as_of,categories=facts['spending']['categories'],
        bills=active_bills,protected_categories=protected,subscription_states=subscription_states)
    events=normalized_shocks(past,as_of=as_of) if shocks is None else shocks
    facts.update(budgets=budgets,bills_calendar=calendar,savings_finder=opportunities,shocks=events)
    facts['personalised_insights']=personalised_insights(facts,budgets=budgets,calendar=calendar,
        opportunities=opportunities,shocks=events,feedback=feedback)
    return facts
