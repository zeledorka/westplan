"""Bounded dated liquidity evidence. Estimates are explicit; this module never moves funds."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_FLOOR
from .categorisation import committed_transactions
from statistics import mean
from .bills import bills_calendar, recurring_series
from .data import normalize_merchant
from .contracts import category
from .savings import monthly_flows

HORIZON_DAYS=90


def cashflow_evidence(txns, *, as_of, accounts, pending_expenses=(), subscription_states=None, facts=None):
    """Server-owned, privacy-minimised evidence; no raw narratives or account identifiers."""
    from .insights import financial_insights
    txns=committed_transactions([t for t in txns if t.date<=as_of])
    if facts is None:facts=financial_insights(txns,as_of=as_of,accounts=accounts)
    funding=[b for b in facts['balances'] if b['kind']=='transaction']
    reasons=[]
    if len(funding)!=1 or funding[0]['balance'] is None:
        return {'available':False,'reasons':['Exactly one known funding transaction account is required.']}
    account=funding[0];aid=account['account_id'];end=as_of+timedelta(days=HORIZON_DAYS)
    calendar=bills_calendar(txns,as_of=as_of,pending_expenses=pending_expenses,
                            subscription_states=subscription_states,horizon_days=HORIZON_DAYS)
    events=[]
    for bill in calendar['items']:
        if bill['account_id'] not in {None,aid} or bill['already_paid']:continue
        if bill['status']=='anticipated':
            reasons.append('An anticipated obligation needs confirmed amount/timing.');continue
        due=max(as_of,date.fromisoformat(bill['expected_date']))
        if due>end:
            reasons.append('A known obligation lies beyond the bounded forecast.');continue
        events.append({'date':due.isoformat(),'amount':bill['expected_amount'],'kind':'obligation',
                       'category':category(bill['category']),'estimated':not bill['confirmed']})
    active={b['merchant'] for b in calendar['items'] if b['account_id']==aid and b['status']=='upcoming' and b['source']=='recurring_estimate'}
    # A confirmed cancellation or explicitly completed instalment is not another
    # future bill hidden inside the ordinary-spending estimate.
    import re
    for bill in calendar['items']:
        if bill['account_id']==aid and (subscription_states or {}).get(bill['subscription_id'],{}).get('status')=='cancelled':
            state=subscription_states[bill['subscription_id']]
            if state.get('effective_on','9999')<=as_of.isoformat():active.add(bill['merchant'])
    last_debt={}
    for t in txns:
        if t.account_id==aid and t.category=='Buy Now Pay Later':last_debt[normalize_merchant(t.description)]=t
    for merchant,t in last_debt.items():
        label=re.search(r'\((\d+)/(\d+)\)',t.description)
        if label and label.group(1)==label.group(2):active.add(merchant)
    monthly=monthly_flows(txns);months={f.month for f in monthly}
    if len(months)<3:reasons.append('At least three complete observed months are required for ordinary spending.')
    ordinary=[sum(-t.amount for t in txns if t.account_id==aid and t.is_expense and
                  t.date.strftime('%Y-%m')==m and normalize_merchant(t.description) not in active) for m in sorted(months)]
    envelope=max(mean(ordinary),mean(ordinary[-3:]),0) if ordinary else 0
    income_proofs=[]
    for series in recurring_series(txns,as_of,'income'):
        if series.account_id!=aid or len(series.occurrences)<6:continue
        amount=min(t.amount for t in series.occurrences[-6:])
        future=series.upcoming(as_of,HORIZON_DAYS)
        if not future:continue
        income_proofs.append({'amount':amount,'period_days':series.period_days,'observations':len(series.occurrences),
                              'assumption':'Lower of the last six observed receipts; estimated, not guaranteed.'})
        events.extend({'date':d.isoformat(),'amount':amount,'kind':'income','estimated':True} for d in future)
    # Source-flagged standing savings transfers also consume funding-account liquidity.
    # They are separate from a newly proposed schedule; no automatic reassignment is assumed.
    from dataclasses import replace
    transfers=[replace(t,type='expense') for t in txns if t.account_id==aid and t.type=='transfer' and t.amount<0 and t.recurring]
    for series in recurring_series(transfers,as_of):
        events.extend({'date':d.isoformat(),'amount':series.typical_amount,'kind':'standing_transfer','estimated':True}
                      for d in series.upcoming(as_of,HORIZON_DAYS))
    return {'available':not reasons,'reasons':sorted(set(reasons)),'as_of':as_of.isoformat(),'end':end.isoformat(),
            'starting_balance':account['balance'],'protected_reserve':account['protected'],'buffer':200.0,
            'ordinary_monthly_envelope':round(envelope,2),'ordinary_daily_estimate':envelope/(365.25/12),
            'income_evidence':income_proofs,'events':sorted(events,key=lambda e:(e['date'],e['kind'])),
            'assumptions':['Forecast is capped at 90 days; later schedule affordability is not established by this projection.',
                'All ordinary spending is estimated from the larger of historical/recent means; no discretionary cuts assumed.',
                'Income is estimated from recorded recurring receipts; same-day debits are stress-tested before credits.',
                'Historical ordinary-spend allowance is a future spending estimate, not a second debit for posted bills.',
                'Recorded standing savings transfers remain in place unless separately changed.']}


def project_schedule(evidence, plans, contribution_dates, *, runway_end, hypothetical_impact=0):
    """Apply actual proposed due dates to shared source evidence, with intermediate minima."""
    if not evidence.get('available'):return {'available':False,'reasons':evidence.get('reasons',['Missing dated source evidence.'])}
    start=date.fromisoformat(evidence['as_of']);cap=date.fromisoformat(evidence['end'])
    completion=max((date.fromisoformat(p['deadline']) for p in plans if p['contribution']>0),default=start)
    confirmed=[date.fromisoformat(e['date']) for e in evidence['events'] if e['kind']=='obligation' and not e['estimated']]
    end=min(cap,max([completion,date.fromisoformat(runway_end)]+confirmed))
    grouped=defaultdict(list)
    for e in evidence['events']:
        if date.fromisoformat(e['date'])<=end:grouped[e['date']].append(e)
    scheduled=defaultdict(float)
    for p in plans:
        for d in contribution_dates[p['goal_ref']]:
            if d<=end:scheduled[d.isoformat()]+=p['contribution']
    initial=evidence['starting_balance']-evidence['protected_reserve']
    balance=initial-hypothetical_impact;minimum=balance;minimum_date=start.isoformat();timeline=[];paid=0.0;max_scale=None
    for offset in range((end-start).days+1):
        day=start+timedelta(days=offset);key=day.isoformat();events=grouped[key]
        income=sum(e['amount'] for e in events if e['kind']=='income')
        out=sum(e['amount'] for e in events if e['kind']!='income')
        ordinary=evidence['ordinary_daily_estimate'] if offset else 0
        contribution=round(scheduled[key],2);paid+=contribution
        before_income=balance-out-ordinary-contribution
        if not paid and before_income<evidence['buffer']:max_scale=0.0
        if paid>0:
            scale=max(0,(before_income+paid-evidence['buffer'])/paid)
            max_scale=scale if max_scale is None else min(max_scale,scale)
        if before_income<minimum:minimum=before_income;minimum_date=key
        balance=before_income+income
        timeline.append({'date':key,'income':round(income,2),'obligations_and_standing_transfers':round(out,2),
                         'ordinary_spending':round(ordinary,2),'contributions':contribution,'closing_cash':round(balance,2)})
    return {'available':True,'as_of':start.isoformat(),'end':end.isoformat(),
            'minimum_cash':float(Decimal(str(minimum)).quantize(Decimal('.01'),rounding=ROUND_FLOOR)),'minimum_date':minimum_date,'ending_cash':round(balance,2),
            'protected_reserve':evidence['protected_reserve'],'buffer':evidence['buffer'],
            'total_contributions':round(paid,2),'timeline':timeline,
            'maximum_evaluated_monthly_equivalent':round(sum(p['monthly_equivalent'] for p in plans)*max_scale,2) if max_scale is not None and paid>0 else None,
            'capacity_scope':'Estimated bound for these fixed contribution dates within the evaluated horizon; not sustainable capacity or approval.',
            'full_schedule_covered':all(p['completion_date'] and (not p['contribution'] or p['deadline']<=end.isoformat()) for p in plans),
            'assumptions':evidence['assumptions']}


def cash_funded_stress(evidence, plans, contribution_dates, *, runway_end, hypothetical_impact=0):
    """Finite-plan funding proof: no future income credited, reserves remain unavailable."""
    stressed={**evidence,'events':[e for e in evidence.get('events',[]) if e['kind']!='income'],
              'assumptions':evidence.get('assumptions',[])+['Stress proof credits no future income.']}
    result=project_schedule(stressed,plans,contribution_dates,runway_end=runway_end,hypothetical_impact=hypothetical_impact)
    result.pop('timeline',None)
    return result
