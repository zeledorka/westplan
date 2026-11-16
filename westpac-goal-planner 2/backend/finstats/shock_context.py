"""Adaptive, causal financial events around the six compatible legacy detectors."""
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from functools import lru_cache
import math

from .bills import recurring_series, bills_calendar
from .data import primary_account
from .event_model import event, event_id
from .income_shocks import detect_income_interruptions
from .pattern_shocks import detect_persistent_changes
from .insights import financial_insights
from .savings import monthly_flows
from .stats import median, mean
from .shocks import (detect_large_single_spends, detect_new_merchant_big_amount, detect_bursts,
    detect_category_spikes, detect_recurring_bill_changes, check_runway)


def _floor(past, category, *, new_merchant=False):
    expenses=[t.spend for t in past if t.is_expense and t.amount<0 and not t.recurring
              and (new_merchant or t.category==category)]
    if not expenses:
        expenses=[t.spend for t in past if t.is_expense and t.amount<0]
    flows=monthly_flows(past)
    income=mean([f.income for f in flows])
    # $10 is a noise guard, not a hardship threshold. History and income set materiality.
    return round(max(10,median(expenses)*(6 if new_merchant else .5),income*(.03 if new_merchant else .01)),2)


def _known_recurring(t,past):
    history=[p for p in past if p.account_id==t.account_id and p.merchant==t.merchant]
    if t.recurring and (not history or abs(t.spend-median([p.spend for p in history]))<=.2*median([p.spend for p in history])):
        return True
    for s in recurring_series(history,t.date):
        if t.date in s.upcoming(t.date-timedelta(days=1),4) and abs(t.spend-s.typical_amount)<=.2*s.typical_amount:
            return True
    return False


def expectedness_for(t,past,*,planned_expenses=(),pending_expenses=()):
    for p in planned_expenses:
        if p.known_on<=t.date and p.transaction_id==t.transaction_id and abs(p.amount-t.spend)<.01:
            return 'expected','explicit_plan'
    for p in pending_expenses:
        if p.known_on<=t.date and p.transaction_id==t.transaction_id and p.status=='unpaid' and abs(p.amount-t.spend)<=.2*p.amount:
            return 'expected','confirmed_bill'
    if _known_recurring(t,past):
        return 'expected','recurring_evidence'
    return 'unknown','insufficient_evidence'


@lru_cache(maxsize=32)
def _core_events(txns,as_of):
    ids=[t.transaction_id for t in txns]
    if len(set(ids))!=len(ids) or any(not i for i in ids):
        raise ValueError('Shock normalization requires unique, nonempty transaction IDs')
    if len({t.user_id for t in txns if t.user_id})>1:
        raise ValueError('Provide one customer\'s transactions')
    by_id={t.transaction_id:t for t in txns}
    found={}
    accounts=defaultdict(list)
    for t in txns:
        accounts[t.account_id].append(t)

    def attach(s,observed,account,history=None,threshold=None):
        rows=[by_id[i] for i in s.transaction_ids]
        single=len(rows)==1
        key=('transaction',account,rows[0].transaction_id) if single else (
            s.type,account,s.details.get('category',''),s.date)
        ref=event_id(*key)
        if not single and s.type=='category_spike':
            row_ids=set(s.transaction_ids)
            for existing in found.values():
                if (existing['event_type']=='activity_pattern' and 'burst' in existing['detectors']
                    and set(existing['transaction_ids'])<=row_ids
                    and len(set(existing['categories'])|{t.category for t in rows})==1
                    and (existing['event_amount'] or 0)>=.6*sum(t.spend for t in rows)):
                    ref=existing['event_ref']
                    existing['transaction_ids']=sorted(set(existing['transaction_ids'])|row_ids)
                    existing['event_amount']=round(sum(by_id[i].spend for i in existing['transaction_ids']),2)
                    break
        if ref not in found:
            found[ref]=event(key,'transaction_anomaly' if single else 'activity_pattern',
                min(t.date for t in rows),observed,sum(t.spend for t in rows),
                [t.category for t in rows],s.transaction_ids,(s.type,))
            found[ref]['_account_id']=account
        e=found[ref]
        e['detectors']=sorted(set(e['detectors'])|{s.type})
        # New-merchant ratios must use the adaptive floor, not the $1 candidate floor.
        score=rows[0].spend/threshold if s.type=='new_merchant_big_amount' and threshold else s.score
        e['evidence'].append({'detector':s.type,'score':score if math.isfinite(score) else None,
                              'score_is_unbounded':not math.isfinite(score)})
        e.update(statistical_anomaly=True,confidence='moderate' if single and len(history or [])<8 else 'higher')
        if history is not None:
            values=[t.spend for t in history if t.is_expense and t.amount<0 and t.category==rows[0].category]
            e['historical'].update(category_observations=len(values),category_median=round(median(values),2),adaptive_floor=threshold,
                amount_vs_category_median=round(rows[0].spend/median(values),2) if median(values)>0 else None)
        if 'trailing_median' in s.details:
            e['historical'].update(baseline_weekly=s.details['trailing_median'],recent_weekly=s.amount)
        if 'count' in s.details:
            e['historical'].update(daily_purchase_count=s.details['count'],baseline_daily_count=s.details['baseline_median'],daily_count_threshold=s.details['threshold'])
        e['explanation']='This amount differs from this customer\'s own spending history.' if single else 'Several purchases form an unusual activity pattern.'
        return e

    for account,rows in accounts.items():
        expenses=[t for t in rows if t.is_expense and t.amount<0]
        # Existing robust detectors supply candidates. Disable whole-history recurrence
        # exclusions; expectedness is checked using ONLY observations before each event.
        for detector,new in ((detect_large_single_spends,False),(detect_new_merchant_big_amount,True)):
            for s in detector(rows,floor=1,exclude_recurring=False):
                t=by_id[s.transaction_ids[0]]
                prior=[p for p in rows if p.date<t.date]
                threshold=_floor(prior,t.category,new_merchant=new)
                if len([p for p in prior if p.is_expense])>=(1 if new else 3) and t.spend>=threshold and not _known_recurring(t,prior):
                    e=attach(s,t.date,account,prior,threshold)
                    if len([p for p in prior if p.is_expense])<3:
                        e.update(event_type='limited_history_review',statistical_anomaly=False,confidence='limited')
                        e['explanation']='A large new-merchant payment needs clarification; the available history is very limited.'
        for s in detect_bursts(rows):
            attach(s,s.date,account)
        for s in detect_category_spikes([t for t in rows if not t.recurring],exclude_recurring=False):
            observed=max(by_id[i].date for i in s.transaction_ids)
            prior=[t for t in rows if t.date<observed]
            if not all(_known_recurring(by_id[i],[p for p in rows if p.date<by_id[i].date]) for i in s.transaction_ids):
                attach(s,observed,account,prior)
        merchants=defaultdict(list)
        for t in expenses:
            merchants[t.merchant].append(t)
        for merchant_rows in merchants.values():
            for index in range(2,len(merchant_rows)):
                prefix=merchant_rows[:index+1]
                day=prefix[-1].date
                for s in detect_recurring_bill_changes(prefix):
                    if s.date==day:
                        e=attach(s,day,account,prefix[:-1])
                        e['historical'].update(previous_amount=s.details['previous'],period_days=s.details['period_days'])
        # Limited-history candidates are explicitly uncertain, not asserted statistical anomalies.
        for t in expenses:
            prior=[p for p in rows if p.date<t.date]
            previous=[p for p in prior if p.is_expense and p.amount<0]
            if 3<=len(previous)<8 and not t.recurring and t.spend>=max(10,median([p.spend for p in previous])*5):
                ref=event_id('transaction',account,t.transaction_id)
                if ref not in found:
                    e=event(('transaction',account,t.transaction_id),'limited_history_review',t.date,t.date,t.spend,
                        (t.category,),(t.transaction_id,),('limited_history_review',))
                    e['_account_id']=account
                    e['historical']={'observations':len(previous),'typical_amount':median([p.spend for p in previous])}
                    e['explanation']='This expense is large relative to a short available history; clarification is needed.'
                    found[ref]=e
        episode=None
        days=sorted({t.date for t in rows}|{as_of})
        for day in days:
            past=[t for t in rows if t.date<=day]
            closing=[t.balance_after for t in past if t.balance_after is not None]
            if not closing:
                continue
            bills=recurring_series(past,day)
            income=recurring_series(past,day,'income')
            risk=check_runway(closing[-1],day,bills,account_id=account,income_series=income)
            if risk:
                if episode is None:
                    episode=event(('liquidity_episode',account,day),'liquidity_pressure',day,day,None,
                        methods=('runway',),status='projection')
                    episode['_account_id']=account
                    episode['historical']['pressure_observations']=0
                    found[episode['event_ref']]=episode
                episode['historical']['pressure_observations']+=1
                episode['observed_on']=day.isoformat()
                episode['impact']['projected_buffer_shortfall']=risk.score
                episode['pattern_stage']='sustained' if episode['historical']['pressure_observations']>=3 else 'emerging'
                episode.update(confidence='moderate',severity='cashflow_risk')
                episode['explanation']='Known recurring obligations may leave this account below its cash buffer.'
            elif not check_runway(closing[-1],day,bills,buffer=400,account_id=account,income_series=income):
                episode=None
    patterns=detect_persistent_changes(txns,as_of=as_of)
    for p in patterns:
        # A sustained episode owns ordinary contributing alerts. Preserve security bursts
        # and purchases far beyond even the changed pattern as distinct review events.
        members=set(p['transaction_ids'])
        usual=median([by_id[i].spend for i in members]) if members else 0
        for ref,e in list(found.items()):
            covered=bool(e['transaction_ids']) and set(e['transaction_ids'])<=members
            weekly=e['event_type']=='activity_pattern' and e['detectors']==['category_spike']
            ordinary_purchase=(p['event_type']=='sustained_spending_change'
                and e['event_type'] in {'transaction_anomaly','limited_history_review'}
                and (e['event_amount'] or 0)<=3*usual)
            if covered and (weekly or ordinary_purchase):
                p['detectors']=sorted(set(p['detectors'])|set(e['detectors']))
                p['evidence']+=e['evidence'];del found[ref]
        found[p['event_ref']]=p
    return list(found.values())


def normalized_shocks(txns, *, as_of, accounts=(), pending_expenses=(), planned_expenses=(),
                      event_evidence=(), event_states=None, known_holidays=(), facts=None, calendar=None):
    past=tuple(sorted((t for t in txns if t.date<=as_of),key=lambda t:t.date))
    facts=facts or financial_insights(past,as_of=as_of,accounts=accounts)
    calendar=calendar or bills_calendar(past,as_of=as_of,pending_expenses=pending_expenses)
    events=deepcopy(_core_events(past,as_of))
    events+=detect_income_interruptions(past,as_of=as_of,known_holidays=known_holidays)
    by_id={t.transaction_id:t for t in past}
    annotations={e.transaction_id:e for e in event_evidence if e.known_on<=as_of}
    # Explicit evidence may identify a meaningful event even when sparse statistics cannot.
    for tid,a in annotations.items():
        t=by_id.get(tid)
        if t and t.is_expense and not any(tid in e['transaction_ids'] and e['event_type']!='activity_pattern' for e in events):
            e=event(('transaction',t.account_id,tid),'confirmed_expense',t.date,as_of,t.spend,(t.category,),(tid,),('explicit_evidence',))
            e['_account_id']=t.account_id;events.append(e)
    for b in calendar['items']:
        if b['source']=='explicit_obligation' and not b['already_paid']:
            known=next(p.known_on for p in pending_expenses if 'pending-'+p.event_id==b['bill_id'])
            e=event(('obligation',b['bill_id']),'outstanding_obligation' if b['confirmed'] else 'anticipated_expense',
                    known,known,b['expected_amount'],(b['category'],),methods=('explicit_obligation',),
                    status='outstanding' if b['confirmed'] else 'anticipated')
            e['impact']['additional_future_debit']=b['expected_amount'] if b['confirmed'] else 0
            e['impact']['already_in_projection']=b['confirmed']
            e['_account_id']=None;e['confidence']='higher' if b['confirmed'] else 'limited'
            e['expectedness']='expected' if b['confirmed'] else 'unknown'
            e['expectedness_source']='confirmed_bill' if b['confirmed'] else 'insufficient_evidence'
            e['historical']={'due_date':b['expected_date']}
            e['explanation']='A confirmed obligation is still outstanding.' if b['confirmed'] else 'An anticipated expense is not yet confirmed.'
            events.append(e)
    for e in events:
        rows=[by_id[i] for i in e['transaction_ids'] if i in by_id]
        expenses=[t for t in rows if t.is_expense]
        if expenses:
            assessments=[expectedness_for(t,[p for p in past if p.date<t.date],planned_expenses=planned_expenses,pending_expenses=pending_expenses) for t in expenses]
            if all(a[0]=='expected' for a in assessments):
                e['expectedness']='expected';e['expectedness_source']=assessments[0][1]
            proofs=[annotations[t.transaction_id] for t in expenses if t.transaction_id in annotations]
            if proofs:
                e['expectedness']='unexpected' if any(a.unexpected for a in proofs) else 'expected'
                e['expectedness_source']='explicit_user_or_scenario'
                e['expense_kind']=proofs[0].kind
                e['evidence_source']='explicit_user_or_scenario'
        e['unexpected_confirmed']=e['expectedness']=='unexpected'
        account=e.get('_account_id') or primary_account(past)
        balance=next((b['balance'] for b in facts['balances'] if b['account_id']==account),None)
        reserve=next((b['protected'] for b in facts['balances'] if b['account_id']==account),0)
        runway=facts['cashflow']['runway']
        end=runway['window_end'] if runway and runway['account_id']==account else (as_of+timedelta(days=14)).isoformat()
        due=round(sum(b['expected_amount'] for b in calendar['items'] if b['status']=='upcoming'
                      and b['expected_date']<=end and (b['account_id']==account or b['account_id'] is None and account==primary_account(past))),2)
        projected=round(balance-due,2) if balance is not None else None
        liquid=max(0,balance-reserve) if balance is not None else None
        # For posted expenses, compare against PRE-event resources. Never debit them again.
        ratio=round(e['event_amount']/(liquid+sum(t.spend for t in expenses)),4) if e['event_amount'] and liquid is not None and liquid+sum(t.spend for t in expenses)>0 else None
        cap=facts['cashflow']['savings_capacity']
        e['impact'].update(current_balance=balance,protected_reserve=reserve,upcoming_obligations=due,
            projected_balance=projected,unreserved_projected_balance=round(projected-reserve,2) if projected is not None else None,liquidity_ratio=ratio,comfortable=cap['comfortable'],stretch=cap['stretch'],
            monthly_surplus=facts['cashflow']['average_surplus'],income_variability=facts['income']['variability_cv'],
            amounts_additive=False)
        e['potential_disruption']=('unknown' if projected is None else 'high' if projected-reserve<200 or (ratio or 0)>.5 else
            'moderate' if (ratio or 0)>.2 or (facts['income']['variability_cv'] or 0)>.3 and (e['event_amount'] or 0)>max(0,facts['cashflow']['average_surplus']) else 'low')
        if e['expectedness']!='unknown' and e['questions']==['Was this expected?']:
            e['questions']=[]
        if e['potential_disruption']=='high':
            e['severity']='cashflow_risk'
        if expenses:
            e['_comparison_key']=event_id(account,expenses[0].merchant,expenses[0].category)
        # Preserve matching v1.1 individual-event insight feedback across the ID upgrade.
        # Never propagate a dismissal to merely overlapping or newly distinct events.
        if e['event_type'] in {'transaction_anomaly','limited_history_review','activity_pattern'}:
            from itertools import combinations
            from hashlib import sha256
            legacy=[m for m in e['detectors'] if m in {'large_single_spend','new_merchant_big_amount','burst','category_spike','recurring_bill_change'}]
            e['_legacy_event_refs']=[sha256((e['date']+str(sorted(e['transaction_ids']))+str(sorted(subset))).encode()).hexdigest()[:16]
                                     for size in range(1,len(legacy)+1) for subset in combinations(legacy,size)]
        e['evidence_version']=event_id(e['event_type'],e['transaction_ids'],e['event_amount'],e['categories'],e['pattern_stage'],e['potential_disruption'],e['historical'].get('missed_cycles'),
            math.ceil(max(0,200-projected)/200) if projected is not None and e['event_type']=='liquidity_pressure' else None)
    from .event_lifecycle import apply_lifecycle
    events=apply_lifecycle(events,event_states or {},as_of=as_of)
    return sorted(events,key=lambda e:(e['date'],e['event_ref']))
