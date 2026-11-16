"""Regular-credit interruption monitoring. No payroll API or assumed invoice payday."""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta
from .data import normalize_merchant
from .stats import median, mad, mean, std
from .event_model import event


def business_day(day, holidays=()):
    while day.weekday()>=5 or day in holidays:
        day += timedelta(days=1)
    return day


def grace_end(day, holidays=(), business_days=2):
    day=business_day(day,holidays)
    for _ in range(business_days):
        day=business_day(day+timedelta(days=1),holidays)
    return day


def detect_income_interruptions(txns, *, as_of, known_holidays=()):
    groups=defaultdict(list)
    for t in sorted((t for t in txns if t.date<=as_of and t.is_income and t.amount>0),key=lambda t:t.date):
        if normalize_merchant(t.description)=='INTEREST PAID':
            continue
        groups[(t.account_id,t.merchant)].append(t)
    result=[]
    for (account,merchant),rows in groups.items():
        if len(rows)<4:
            continue
        train=rows[:4]
        gaps=[(b.date-a.date).days for a,b in zip(train,train[1:])]
        period=median(gaps)
        monthly=27<=period<=32
        if not (6<=period<=8 or 12<=period<=16 or monthly):
            continue
        if sum(abs(g-period)<=3 for g in gaps)/len(gaps)<.8:
            continue
        amounts=[t.amount for t in train]
        if median(amounts)<=0 or mad(amounts)/median(amounts)>.15 or std(amounts)/mean(amounts)>.3:
            continue
        anchor_day=round(median([t.date.day for t in train]))
        month_end=all(t.date.day==monthrange(t.date.year,t.date.month)[1] for t in train)
        def advance(day):
            if not monthly:
                return day+timedelta(days=round(period))
            year,month=(day.year+1,1) if day.month==12 else (day.year,day.month+1)
            return date(year,month,monthrange(year,month)[1] if month_end else min(anchor_day,monthrange(year,month)[1]))
        expected=advance(train[-1].date)
        history=list(amounts)
        remaining=list(rows[4:])
        missing=None
        reduction=None
        while expected<=as_of:
            end=grace_end(expected,known_holidays)
            next_expected=advance(expected)
            # Late arrivals before the next cycle retain the same interruption identity.
            cycle_end=min(next_expected-timedelta(days=3),as_of)
            arrivals=[t for t in remaining if expected-timedelta(days=3)<=t.date<=cycle_end]
            baseline=median(history[-6:])
            if arrivals:
                for t in arrivals:
                    remaining.remove(t)
                received=sum(t.amount for t in arrivals)
                arrived=max(t.date for t in arrivals)
                shortfall=max(0,baseline-received)
                lower=baseline-max(.2*baseline,3*mad(history[-6:]))
                if arrived>end:
                    late=missing or event(('income_gap',account,merchant,expected),'delayed_income',expected,arrived,
                        baseline,('Income',),methods=('income_schedule',),status='income_delayed')
                    late.update(event_type='delayed_income',status='income_delayed',observed_on=arrived.isoformat(),
                                transaction_ids=[t.transaction_id for t in arrivals])
                    late['impact']['already_in_balance']=True
                    late['historical'].update(expected_date=expected.isoformat(),grace_end=end.isoformat(),arrival_date=arrived.isoformat())
                    if missing:
                        result.remove(missing)
                    result.append(late);missing=None
                if received<lower:
                    if reduction is None:
                        reduction=event(('income_reduction',account,merchant,expected),'reduced_income',arrived,arrived,
                            shortfall,('Income',),methods=('income_amount',),status='income_received')
                        reduction['historical']['reduced_cycles']=0
                        result.append(reduction)
                    reduction['historical']['reduced_cycles']+=1
                    reduction['historical'].update(expected_amount=baseline,received_amount=received,
                                                   shortfall=round(shortfall,2),period_days=period)
                    reduction['transaction_ids'] += [t.transaction_id for t in arrivals]
                    reduction['pattern_stage']='sustained' if reduction['historical']['reduced_cycles']>=3 else 'emerging'
                    reduction['event_amount']=round(shortfall,2)
                else:
                    reduction=None
                    history.append(received)
            elif as_of>end:
                if missing is None:
                    missing=event(('income_gap',account,merchant,expected),'missing_income',expected,end+timedelta(days=1),
                        baseline,('Income',),methods=('income_schedule',),status='income_missing')
                    missing['historical'].update(expected_date=expected.isoformat(),grace_end=end.isoformat(),missed_cycles=0)
                    result.append(missing)
                missing['historical']['missed_cycles']+=1
                missing['event_amount']=round(baseline*missing['historical']['missed_cycles'],2)
                missing['pattern_stage']='sustained' if missing['historical']['missed_cycles']>=3 else 'emerging'
            expected=next_expected
        for e in result:
            if e.get('_income_source') is not None:
                continue
            # Only events just created for this source lack internal source metadata.
            e['_income_source']=(account,merchant)
            e['_account_id']=account
            e['statistical_anomaly']=True
            e['confidence']='higher' if len(history)>=6 else 'moderate'
            e['historical'].update(training_observations=4,period_days=period)
            e['questions']=['Has this income source changed, ended, or been paid elsewhere?']
            e['explanation']={'missing_income':'A previously regular credit has not arrived after its expected window.',
                'delayed_income':'A regular credit arrived after its expected window.',
                'reduced_income':'A regular credit was materially smaller than its established baseline.'}[e['event_type']]
            e['uncertainty']='Recurring credits are inferred, not payroll contracts. Weekends and supplied holidays are handled; other public holidays are unknown.'
    return result
