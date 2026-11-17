"""Reconciled ledgers and explicit target-level detection labels."""
from dataclasses import replace
from datetime import date, timedelta
from finstats.data import Transaction
from planning.schemas import PlannedExpense, PostedEventEvidence

START=date(2026,1,5)
END=START+timedelta(weeks=24,days=-1)


def base_rows():
    rows=[]
    def add(day,amount,cat,description,essential=False,recurring=False,kind=None,account='cash'):
        rows.append(Transaction(day,description,amount,cat,kind or ('income' if amount>0 else 'expense'),
            essential,recurring,account_id=account,user_id='evaluation',transaction_id=f't{len(rows)}'))
    for week in range(24):
        d=START+timedelta(weeks=week)
        add(d,1000,'Income','EMPLOYER PAYROLL',recurring=True)
        for offset in (1,2,4,5):
            add(d+timedelta(days=offset),-175,'Groceries','GROCER',True)
        add(d+timedelta(days=2),-20,'Shopping','STORE '+chr(65+week),False)
        add(d+timedelta(days=4),-180,'Eating out','DINING',False)
        add(d+timedelta(days=6),-100,'Transfers','TO SAVINGS',kind='transfer')
        add(d+timedelta(days=6),100,'Transfers','FROM CASH',kind='transfer',account='savings')
    return rows


def ledger(rows,opening=600):
    balances={'cash':opening,'savings':1000}
    out=[]
    for t in sorted(rows,key=lambda t:t.date):
        balances[t.account_id]=round(balances[t.account_id]+t.amount,2)
        out.append(replace(t,balance_after=balances[t.account_id]))
    return out


def scenarios():
    base=base_rows()
    target=Transaction(END,'EMERGENCY CLINIC',-1850,'Pets','expense',True,
        account_id='cash',user_id='evaluation',transaction_id='target')
    def case(name,rows,positive=True,ids=('target',),types=(),as_of=END,opening=600,expectedness=None,**kwargs):
        return dict(name=name,txns=ledger(rows,opening),as_of=as_of,positive=positive,
                    target_ids=ids,target_types=types,expectedness=expectedness,kwargs=kwargs)
    reasons={
        'emergency_vet':'An explicitly annotated emergency and unusual essential debit; already reflected in cash.',
        'planned_holiday':'Unusual size remains review-worthy; an explicit pre-existing plan makes it expected and ample cash reduces disruption.',
        'annual_insurance':'An unchanged annual recurring premium is expected, without a new target anomaly.',
        'ordinary_rent':'An unchanged explicitly recurring rent debit is an ordinary obligation.',
        'internal_transfer':'Moving existing funds between own accounts is not new expense.',
        'missing_salary':'An established weekly credit is absent after its business-day grace window.',
        'delayed_salary':'The regular credit arrives after grace; the missing episode becomes delayed, without another debit.',
        'reduced_salary':'A regular credit is 50% below its established amount, beyond the reduction tolerance.',
        'irregular_freelance':'Uneven timing and amounts do not establish a reliable missing-payday schedule.',
        'discretionary_burst':'Twelve small same-day purchases exceed this account’s normal daily frequency.',
        'persistent_essential_increase':'Several complete weeks of grocery costs exceed the prior weekly baseline.',
        'affordable_unusual_purchase':'An unusual purchase against personal spending history; large liquid resources imply low disruption, expectedness unknown.',
        'cash_straining_purchase':'The same unusual purchase consumes much more of available resources; expectedness remains unknown.',
        'overlapping_detectors':'A single unusual purchase can trigger several methods but must retain one transaction-event identity.',
    }
    result=[case('emergency_vet',base+[target],opening=2000,expectedness='unexpected',
                 event_evidence=(PostedEventEvidence('target',END,'emergency_veterinary'),))]
    holiday=replace(target,description='PLANNED HOLIDAY BOOKING',amount=-2000,category='Travel',essential=False)
    result.append(case('planned_holiday',base+[holiday],opening=30000,expectedness='expected',
        planned_expenses=(PlannedExpense('plan','target',2000,'Travel',END-timedelta(days=10),END),)))
    insurance=replace(target,description='ANNUAL INSURANCE',amount=-500,category='Insurance',recurring=True)
    history=[replace(insurance,date=date(y,6,21),transaction_id='annual'+str(y)) for y in (2024,2025)]
    result.append(case('annual_insurance',history+base+[insurance],False,opening=30000,expectedness='expected'))
    rent=replace(target,description='RENT',amount=-600,category='Housing',recurring=True)
    result.append(case('ordinary_rent',base+[rent],False,opening=30000,expectedness='expected'))
    transfer=replace(target,description='OWN ACCOUNT TRANSFER',amount=-500,category='Transfers',type='transfer')
    result.append(case('internal_transfer',base+[transfer],False))
    result.append(case('missing_salary',base,ids=(),types=('missing_income',),as_of=END+timedelta(days=5)))
    late=replace(target,date=END+timedelta(days=4),description='EMPLOYER PAYROLL',amount=1000,category='Income',type='income')
    result.append(case('delayed_salary',base+[late],ids=(),types=('delayed_income',),as_of=END+timedelta(days=4)))
    lower=replace(late,date=END+timedelta(days=1),amount=500)
    result.append(case('reduced_salary',base+[lower],ids=(),types=('reduced_income',),as_of=END+timedelta(days=2)))
    freelance=[t for t in base if not t.is_income]
    for index,offset in enumerate((0,9,33,52,80,108,132,160)):
        freelance.append(replace(late,date=START+timedelta(days=offset),amount=500+index*150,transaction_id='invoice'+str(index),description='FREELANCE CLIENT'))
    result.append(case('irregular_freelance',freelance,False,ids=(),types=('missing_income',),as_of=END+timedelta(days=5),opening=30000))
    burst=[replace(target,description='ONLINE '+chr(65+i),amount=-12,category='Shopping',transaction_id='target'+str(i)) for i in range(12)]
    result.append(case('discretionary_burst',base+burst,ids=tuple(t.transaction_id for t in burst)))
    elevated=[replace(t,amount=-280) if t.category=='Groceries' and t.date>=END-timedelta(weeks=4,days=6) else t for t in base]
    result.append(case('persistent_essential_increase',elevated,ids=(),types=('sustained_spending_change',),opening=30000))
    purchase=replace(target,amount=-500,description='ONE LARGE PURCHASE',category='Shopping',essential=False)
    result.append(case('affordable_unusual_purchase',base+[purchase],opening=30000,expectedness='unknown'))
    result.append(case('cash_straining_purchase',base+[purchase],opening=600,expectedness='unknown'))
    multiple=replace(purchase,amount=-2000)
    result.append(case('overlapping_detectors',base+[multiple],opening=30000))
    for item in result:
        item['reason']=reasons[item['name']]
    return result
