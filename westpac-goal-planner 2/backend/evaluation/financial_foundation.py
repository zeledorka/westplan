"""Offline reproduction: python -m evaluation.financial_foundation --output /tmp/results.json.
Only temporary stores are mutated; source balances/history and provider settings are untouched.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse,calendar,hashlib,json
from finstats.data import load_transactions
from finstats.categorisation import classified_transactions
from finstats.savings import savings_capacity
from planning.schemas import Goal,AccountSnapshot
from planning.service import BankingService
from planning.store import DemoStore
from planning.goal_assessment import GoalSafetyError,assess_goal
from planning.agent_tools import add_months
ROOT=Path(__file__).resolve().parents[1]


def summarize(a):
    s=a['safety_assessment']
    return {'status':a['status'],'findings':s.get('findings',[])+[f for row in s.get('strategies',[]) for f in row['findings']],
            'schedule':a.get('schedule'),'dated_cashflow':a.get('calculation',{}).get('dated_cashflow')}


def backtest(profile,txns):
    aid=next(a['account_id'] for a in profile['accounts'] if a['type']=='transaction');rows=[]
    for month in range(4,8):
        origin=date(2026,month,calendar.monthrange(2026,month)[1]);finish=origin+timedelta(days=90)
        if finish>max(t.date for t in txns):continue
        past=[t for t in txns if t.date<=origin];cap=savings_capacity(classified_transactions(past))
        if len(cap.smoothed)<3:continue
        start=[t for t in past if t.account_id==aid][-1].balance_after
        future=defaultdict(float)
        for t in txns:
            if t.account_id==aid and origin<t.date<=finish:future[t.date]+=t.amount
        choices={'comfortable':cap.comfortable,'stretch':cap.stretch,
                 'average_surplus_diagnostic':round(max(0,sum(f.net for f in cap.months)/len(cap.months)),2)}
        for policy,amount in choices.items():
            due={add_months(origin,i) for i in range(1,4)};cash=start;minimum=start;paid=0
            for offset in range(1,91):
                day=origin+timedelta(days=offset)
                if day in due:cash-=amount;paid+=amount
                cash+=future[day];minimum=min(minimum,cash)
            rows.append({'customer':profile['first_name'],'origin':origin.isoformat(),'policy':policy,'contribution':amount,
                'minimum_realised_cash':round(minimum,2),'negative_cash':minimum<0,'below_200_buffer':minimum<200,
                'ending_cash':round(cash,2),'reserve_depletion':round(max(0,start-cash),2),'contributions':round(paid,2)})
    return rows


def evaluate():
    files=[ROOT/'data/mock_users.json',ROOT/'data/mock_transactions.json'];digests={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    result={'customers':{},'backtests':[],'policy':'Capacity formula unchanged. A finite complete schedule can use a separately verified current-cash proof with no future income, confirmed reserves and buffer plus observed volatility. Other schedules retain conservative capacity checks. The horizon bound alone is never approval.',
        'backtest_limits':['Recorded cash transactions, including standing transfers; missing/missed obligations are not observable.',
            'Daily net movements, not intraday solvency. Historical coverage uses existing transaction-boundary rules.',
            'Diagnostic sample across all five customers; not sufficient to recalibrate approval policy.']}
    for profile in json.loads(files[0].read_text()):
        uid=profile['user_id'];txns=load_transactions(files[1],uid);result['backtests'].extend(backtest(profile,txns))
        if uid not in {'u001','u003'}:continue
        day=date(2026,11,15);raw={'txns':txns,'as_of':day,'accounts':tuple(AccountSnapshot(a['account_id'],a['type'],a['balance'],day) for a in profile['accounts'])};original=deepcopy(raw)
        with TemporaryDirectory() as folder:
            bank=BankingService(DemoStore(Path(folder)/'bank.sqlite'),lambda customer:raw)
            ctx=bank.planning_context(uid);aid=next(a.account_id for a in raw['accounts'] if a.kind=='savings')
            row={'capacity':ctx['financial_insights']['cashflow']['savings_capacity'],'cases':{}}
            try:bank.create_goal(uid,Goal('unbacked','holiday',1000,1000,date(2028,11,15)),confirmed=True,expected_revision=0)
            except GoalSafetyError as exc:row['cases']['without_backing']=summarize(exc.assessment)
            reserve=1000 if uid=='u001' else 15000
            bank.designate_savings(uid,aid,1000,reserve,confirmed=True,reserves_confirmed=True,expected_revision=0,expected_financial_version=ctx['state_version'])
            row['designation_assumption']={'available':1000,'protected':reserve,'confirmed':True,'test_only':True}
            bank.create_goal(uid,Goal('funded','holiday',1000,1000,date(2028,11,15)),confirmed=True,expected_revision=1)
            goal=bank.store.read(uid,day)[1]['goals']['funded'];row['cases']['fully_funded']=summarize(goal['financial_assessment'])
            for label,target in [('modest_contributions',600),('unaffordable',100000)]:
                try:
                    bank.create_goal(uid,Goal(label,'holiday',target,0,date(2028,11,15)),confirmed=True,expected_revision=2)
                    raise AssertionError('Unexpected acceptance; investigate before changing expected results')
                except GoalSafetyError as exc:row['cases'][label]=summarize(exc.assessment)
            before=bank.store.read(uid,day)
            try:
                balance=next(a.balance for a in raw['accounts'] if a.account_id==aid)
                bank.designate_savings(uid,aid,1000,balance-500,confirmed=True,reserves_confirmed=True,expected_revision=2,expected_financial_version=bank.planning_context(uid)['state_version'])
                raise AssertionError('Reserve over-allocation accepted')
            except ValueError:row['protected_reserve_overallocation_rejected']=True
            assert bank.store.read(uid,day)==before
            bank.trigger_presenter_event(uid,goal_id='funded',amount=1500,kind='unexpected_bill',confirmed=True,expected_revision=2)
            changed=bank.planning_context(uid);row['cases']['after_unpaid_shock']=summarize(assess_goal(changed,'goal-1',contribution=0.0))
            row['after_shock_current_cash']=changed['financial_insights']['cashflow']['runway']['balance']
            row['after_shock_projected_cash']=changed['financial_insights']['cashflow']['runway']['projected_after_known_pending']
            row['original_goal_and_approval_preserved']=bank.store.read(uid,day)[1]['goals']['funded']==goal
            row['source_finances_unchanged']=raw==original
            result['customers'][profile['first_name']]=row
    result['source_files_unchanged']=all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==v for p,v in digests.items())
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path);args=parser.parse_args()
    text=json.dumps(evaluate(),indent=2,allow_nan=False)
    if args.output:args.output.write_text(text+'\n')
    else:print(text)
