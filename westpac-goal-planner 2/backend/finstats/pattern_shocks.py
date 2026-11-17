"""Small robust episode detectors for sustained category, bill and income changes."""
from collections import defaultdict
from datetime import date, timedelta
from calendar import monthrange
from .shocks import week_start
from .stats import median, mad, mean, std
from .savings import monthly_flows
from .bills import recurring_series
from .event_model import event


def detect_persistent_changes(txns, *, as_of):
    past=[t for t in txns if t.date<=as_of]
    groups=defaultdict(list)
    for t in past:
        if t.is_expense and t.amount<0:
            groups[(t.account_id,t.category)].append(t)
    out=[]
    for (account,category),rows in groups.items():
        first=week_start(min(t.date for t in rows))+timedelta(weeks=1)
        last=week_start(as_of)-(timedelta(0) if as_of.weekday()==6 else timedelta(weeks=1))
        weeks=[];day=first
        while day<=last:
            weeks.append(day);day+=timedelta(weeks=1)
        values=[sum(t.spend for t in rows if week_start(t.date)==w) for w in weeks]
        index=8
        while index<len(weeks):
            baseline=median(values[index-8:index])
            threshold=baseline+max(.3*baseline,3*mad(values[index-8:index]))
            if baseline<=0 or values[index]<=threshold:
                index+=1;continue
            end=index
            while end<len(weeks) and values[end]>threshold:
                end+=1
            if end-index>=2:
                contributing=[t for t in rows if weeks[index]<=t.date<weeks[end-1]+timedelta(weeks=1)]
                e=event(('spending_pattern',account,category,weeks[index]),'sustained_spending_change',weeks[index],
                    weeks[end-1]+timedelta(days=6),sum(t.spend for t in contributing),(category,),
                    [t.transaction_id for t in contributing],('sustained_category',),status='posted')
                e['_account_id']=account
                e.update(statistical_anomaly=True,confidence='moderate',pattern_stage='sustained' if end-index>=4 else 'emerging')
                e['historical']={'baseline_weekly':round(baseline,2),'recent_weekly':round(mean(values[index:end]),2),
                                 'threshold':round(threshold,2),'weeks':end-index,'essential':any(t.essential for t in contributing)}
                e['explanation']='Category spending stayed above its own prior weekly baseline for several complete weeks.'
                out.append(e)
            index=end
    for s in recurring_series(past,as_of):
        if len(s.occurrences)<6:
            continue
        earlier=median([t.spend for t in s.occurrences[:3]])
        recent=median([t.spend for t in s.occurrences[-3:]])
        amounts=[t.spend for t in s.occurrences]
        # Gradual changes are separate from an isolated >10% step already handled by the legacy detector.
        if earlier>0 and recent>1.1*earlier and all(b>=a for a,b in zip(amounts,amounts[1:])) and all(b<=1.1*a for a,b in zip(amounts,amounts[1:])):
            rows=s.occurrences[3:]
            e=event(('bill_trend',s.account_id,s.merchant,rows[0].date),'recurring_cost_trend',rows[0].date,rows[-1].date,
                    recent-earlier,(s.category,),[t.transaction_id for t in rows],('recurring_cost_trend',))
            e['_account_id']=s.account_id
            e.update(statistical_anomaly=True,confidence='moderate',pattern_stage='sustained')
            e['historical']={'previous_amount':earlier,'recent_amount':recent,'observations':len(s.occurrences)}
            e['explanation']='A recurring cost increased gradually across several observed payments.'
            out.append(e)
    flows=monthly_flows(past)
    active={}
    for index in range(5,len(flows)):
        old=[f.income for f in flows[:index-2]]
        new=[f.income for f in flows[index-2:index+1]]
        old_cv=std(old)/mean(old) if mean(old)>0 else 0
        new_cv=std(new)/mean(new) if mean(new)>0 else 0
        conditions={'income_volatility_change':mean(old)>0 and mean(new)>0 and new_cv>old_cv+.15,
                    'persistent_income_reduction':median(old)>0 and all(n<.8*median(old) for n in new)}
        for kind,condition in conditions.items():
            if not condition:
                active.pop(kind,None)
                continue
            end_month=flows[index].month
            year,month=map(int,end_month.split('-'))
            observed=date(year,month,monthrange(year,month)[1])
            if kind not in active:
                start=date.fromisoformat(flows[index-2].month+'-01')
                e=event((kind,start),kind,start,observed,None,('Income',),methods=(kind,),status='pattern')
                e.update(statistical_anomaly=True,confidence='moderate',pattern_stage='sustained')
                e['explanation']='Completed-month income changed relative to the earlier observed months.'
                active[kind]=e;out.append(e)
            active[kind]['historical']={'earlier_income':round(mean(old),2),'recent_income':round(mean(new),2),
                                       'earlier_cv':round(old_cv,4),'recent_cv':round(new_cv,4)}
            active[kind]['observed_on']=observed.isoformat()
    return out
