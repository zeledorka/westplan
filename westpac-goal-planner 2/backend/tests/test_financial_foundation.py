"""Financial-foundation boundaries: real tools/checker, isolated persistence, no model inference."""
from copy import deepcopy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from finstats.data import load_transactions, Transaction
from finstats.categorisation import classified_transactions
from finstats.bills import bills_calendar, bill_insights
from finstats.shocks import RecurringSeries
from planning.demo import AS_OF, demo_contexts
from planning.schemas import Goal, AccountSnapshot, PendingExpense
from planning.service import BankingService
from planning.store import DemoStore, StaleState
from planning.goal_assessment import GoalSafetyError
from planning.agent_tools import PlanningTools
from planning.safety import assess_draft
from tests.test_banking import fixture_inputs
from tests.test_gemini_planner import output

ROOT=Path(__file__).resolve().parents[1]


def customer_inputs(uid):
    profile=next(p for p in json.loads((ROOT/'data/mock_users.json').read_text()) if p['user_id']==uid)
    day=date(2026,11,15)
    return {'txns':load_transactions(ROOT/'data/mock_transactions.json',uid),'as_of':day,
            'accounts':tuple(AccountSnapshot(a['account_id'],a['type'],a['balance'],day) for a in profile['accounts'])}


class DesignationTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=DemoStore(Path(self.temp.name)/'bank.sqlite');self.uid='synthetic-holiday'
        self.raw=fixture_inputs();self.raw['accounts']=tuple(replace(a,protected=0,goal_available=0) if a.kind=='savings' else a for a in self.raw['accounts'])
        self.bank=BankingService(self.store,lambda uid:self.raw)
        self.aid=self.raw['accounts'][1].account_id
    def state(self):return self.store.read(self.uid,self.raw['as_of'])
    def designate(self,available=1000,reserve=8000,**kw):
        return self.bank.designate_savings(self.uid,self.aid,available,reserve,confirmed=True,reserves_confirmed=True,
            expected_revision=self.state()[0],expected_financial_version=self.bank.planning_context(self.uid)['state_version'],**kw)
    def test_designation_preserves_actual_balances_and_records_provenance(self):
        balances=[a.balance for a in self.raw['accounts']];txns=deepcopy(self.raw['txns'])
        self.designate();row=self.bank.savings_funding(self.uid)[0]
        self.assertEqual((row['goal_available'],row['protected'],row['uncommitted_balance']),(1000,8000,300))
        self.assertEqual([a.balance for a in self.raw['accounts']],balances);self.assertEqual(self.raw['txns'],txns)
        self.assertTrue(self.state()[1]['savings_designation_history'][0]['after']['reserves_confirmed'])
    def test_balance_and_reserve_limit_is_atomic(self):
        before=self.state()
        with self.assertRaises(ValueError):self.designate(1301,8000)
        self.assertEqual(self.state(),before)
    def test_known_obligation_has_priority_over_designation(self):
        self.raw['pending_expenses']=(PendingExpense('due',2500,'Pets',AS_OF,AS_OF),)
        with self.assertRaises(ValueError):self.designate(1000,8000)
        self.designate(600,8000)
        self.assertEqual(self.bank.savings_funding(self.uid)[0]['known_obligation_hold'],700)
    def test_cash_covered_obligation_is_not_reserved_twice_in_savings(self):
        self.raw['pending_expenses']=(PendingExpense('covered',500,'Pets',AS_OF,AS_OF),)
        self.designate(1000,8000)
        self.assertEqual(self.bank.savings_funding(self.uid)[0]['known_obligation_hold'],0)

    def test_source_protected_reserve_cannot_be_lowered(self):
        self.raw=fixture_inputs()
        with self.assertRaises(ValueError):self.designate(1000,1000)
    def test_confirmation_scope_and_stale_versions(self):
        ctx=self.bank.planning_context(self.uid)['state_version']
        with self.assertRaises(ValueError):self.bank.designate_savings(self.uid,'foreign-account',100,0,confirmed=True,reserves_confirmed=True,expected_revision=0,expected_financial_version=ctx)
        with self.assertRaises(ValueError):self.bank.designate_savings(self.uid,self.aid,100,0,confirmed=True,reserves_confirmed=False,expected_revision=0,expected_financial_version=ctx)
        self.designate()
        with self.assertRaises(StaleState):self.bank.designate_savings(self.uid,self.aid,100,0,confirmed=True,reserves_confirmed=True,expected_revision=0,expected_financial_version=ctx)
        with self.assertRaises(StaleState):self.bank.designate_savings(self.uid,self.aid,100,0,confirmed=True,reserves_confirmed=True,expected_revision=1,expected_financial_version=ctx)
    def test_other_allocations_remain_backed_and_cannot_be_spent_twice(self):
        self.designate()
        self.bank.create_goal(self.uid,Goal('one','holiday',1000,1000,date(2027,5,31)),confirmed=True,expected_revision=1)
        with self.assertRaises(ValueError):self.designate(999,8000)
        before=self.state()
        with self.assertRaises(GoalSafetyError):self.bank.create_goal(self.uid,Goal('two','holiday',1000,1000,date(2027,5,31)),confirmed=True,expected_revision=before[0])
        self.assertEqual(self.state(),before)
    def test_designation_reassesses_without_changing_goal_or_schedule(self):
        self.designate()
        self.bank.create_goal(self.uid,Goal('one','holiday',500,500,date(2027,5,31)),confirmed=True,expected_revision=1)
        before=deepcopy(self.state()[1]['goals'])
        self.designate(1100,8000)
        self.assertEqual(self.state()[1]['goals'],before)
        self.assertEqual(self.state()[1]['latest_designation_reassessments'][0]['assessment']['status'],'PASS')
    def test_changed_balance_invalidates_financial_version(self):
        ctx=self.bank.planning_context(self.uid)['state_version']
        latest=[t for t in self.raw['txns'] if t.account_id==self.aid][-1]
        self.raw['txns']=[replace(t,balance_after=t.balance_after-100) if t is latest else t for t in self.raw['txns']]
        with self.assertRaises(StaleState):self.bank.designate_savings(self.uid,self.aid,1000,8000,confirmed=True,reserves_confirmed=True,expected_revision=0,expected_financial_version=ctx)


class DebtAndDatesTests(unittest.TestCase):
    def test_bnpl_is_committed_without_changing_total_cashflow(self):
        from finstats.savings import monthly_flows
        for uid in ('u001','u003'):
            raw=customer_inputs(uid);classified=classified_transactions(raw['txns'])
            debt=[t for t in classified if t.category=='Buy Now Pay Later']
            self.assertTrue(debt);self.assertTrue(all(t.essential for t in debt))
            self.assertEqual([round(f.net,2) for f in monthly_flows(raw['txns'])],[round(f.net,2) for f in monthly_flows(classified)])
    def test_monthly_calendar_and_month_end_recurrence(self):
        rows=[Transaction(d,'MONTHLY',-10,'Utilities','expense') for d in (date(2026,1,31),date(2026,2,28),date(2026,3,31))]
        s=RecurringSeries('MONTHLY','Utilities','cash',29.5,3,rows)
        self.assertEqual(s.upcoming(date(2026,3,31),61),[date(2026,4,30),date(2026,5,31)])
        raw=customer_inputs('u003');bill=next(b for b in bill_insights(raw['txns'],raw['as_of']) if b['merchant']=='HOME LOAN REPAYMENT')
        self.assertEqual(bill['next_expected'],'2026-12-05')
    def test_completed_instalments_not_projected_again(self):
        rows=[Transaction(d,f'AFTERPAY SHOP ({i}/4)',-10,'Buy Now Pay Later','expense',transaction_id=str(i)) for i,d in enumerate((date(2026,1,1),date(2026,1,15),date(2026,1,29),date(2026,2,12)),1)]
        cal=bills_calendar(classified_transactions(rows),as_of=date(2026,2,12))
        self.assertFalse(any(b['status']=='upcoming' for b in cal['items']))
    def test_contribution_waits_until_its_actual_due_date(self):
        from planning.context import build_planning_context
        raw=customer_inputs('u001');ctx=build_planning_context(classified_transactions(raw['txns']),as_of=raw['as_of'],accounts=raw['accounts'],goals=(Goal('one','holiday',100,0,date(2027,11,15)),))
        tools=PlanningTools(ctx);calc=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':10.0}]})
        dated=calc['dated_cashflow'];self.assertTrue(dated['available'])
        self.assertTrue(all(r['contributions']==0 for r in dated['timeline'] if r['date']<'2026-12-15'))
        self.assertEqual(next(r['contributions'] for r in dated['timeline'] if r['date']=='2026-12-15'),10)
        fortnight=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':10.0,'frequency':'fortnightly'}]})
        self.assertEqual(next(r['contributions'] for r in fortnight['dated_cashflow']['timeline'] if r['date']=='2026-11-29'),10)
    def test_unchanged_schedule_keeps_its_original_payment_dates(self):
        ctx=demo_contexts()['normal'];ctx['recorded_goal_schedules']=[{'goal_ref':'goal-1','contribution':600.0,'frequency':'monthly','pause_months':0,'schedule_as_of':'2026-08-05'}]
        calc=PlanningTools(ctx).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600.0}]})
        self.assertEqual(calc['plans'][0]['next_contribution_date'],'2026-10-05')
        self.assertEqual(next(row['contributions'] for row in calc['dated_cashflow']['timeline'] if row['date']=='2026-10-05'),600)
        del ctx['recorded_goal_schedules'][0]['schedule_as_of']
        with self.assertRaises(ValueError):PlanningTools(ctx).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600.0}]})

    def test_independent_checker_rejects_tampered_dated_evidence(self):
        ctx=demo_contexts()['normal'];tools=PlanningTools(ctx);calc=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':600.0}]})
        draft={'status':'draft_unverified','activation_allowed':False,'financial_version':ctx['state_version'],'selected_goal_ref':'goal-1','result':output(),'calculations':tools.calculations}
        draft['result']=output(('calculation-1',))
        self.assertEqual(assess_draft(draft,ctx,tools.calculations,expected_goal_ref='goal-1')['status'],'PASS')
        calc['dated_cashflow']['minimum_cash']+=10000
        self.assertEqual(assess_draft(draft,ctx,tools.calculations,expected_goal_ref='goal-1')['status'],'BLOCK')
    def test_negative_intermediate_cash_and_paid_unpaid_accounting(self):
        contexts=demo_contexts()
        for mode in ('vet_paid','vet_unpaid'):
            tools=PlanningTools(contexts[mode]);calc=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':1600.0}]})
            self.assertLess(calc['dated_cashflow']['minimum_cash'],0)
        paid=contexts['vet_paid']['dated_cashflow_evidence'];unpaid=contexts['vet_unpaid']['dated_cashflow_evidence']
        self.assertEqual(sum(e['amount'] for e in paid['events'] if e['kind']=='obligation' and not e['estimated']),0)
        self.assertEqual(sum(e['amount'] for e in unpaid['events'] if e['kind']=='obligation' and not e['estimated']),1850)
    def test_cash_projection_covers_the_whole_committed_term(self):
        ctx=demo_contexts()['normal'];calc=PlanningTools(ctx).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':5000.0}]})
        self.assertEqual(calc['plans'][0]['completion_date'],'2026-10-30')
        self.assertEqual(calc['dated_cashflow']['total_contributions'],10000)
        self.assertFalse(calc['cash_funded_stress']['full_schedule_covered'])

    def test_fully_funded_goal_requires_zero_new_contributions(self):
        ctx=demo_contexts()['normal'];ctx['goals'][0]['target']=ctx['goals'][0]['allocated']
        with self.assertRaises(ValueError):PlanningTools(ctx).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':100.0}]})
        calc=PlanningTools(ctx).calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':0.0}]})
        self.assertEqual(calc['dated_cashflow']['total_contributions'],0)

    def test_short_history_does_not_claim_established_volatility(self):
        from finstats.savings import savings_capacity_from_flows,MonthlyFlow
        from finstats.insights import financial_insights
        raw=customer_inputs('u001');f=financial_insights(raw['txns'],as_of=date(2026,3,4),accounts=())
        self.assertEqual(f['cashflow']['savings_capacity']['smoothed_observations'],1)
        self.assertFalse(f['cashflow']['savings_capacity']['volatility_established'])


class CustomerAdmissionTests(unittest.TestCase):
    def test_actual_customers_can_earmark_funded_goals_without_positive_capacity(self):
        for uid,reserve in [('u001',1000),('u003',15000)]:
            with self.subTest(uid=uid),TemporaryDirectory() as folder:
                raw=customer_inputs(uid);before=deepcopy(raw)
                bank=BankingService(DemoStore(Path(folder)/'bank.sqlite'),lambda customer:raw)
                aid=next(a.account_id for a in raw['accounts'] if a.kind=='savings')
                bank.designate_savings(uid,aid,1000,reserve,confirmed=True,reserves_confirmed=True,expected_revision=0,expected_financial_version=bank.planning_context(uid)['state_version'])
                ctx=bank.planning_context(uid)
                self.assertEqual(ctx['financial_insights']['cashflow']['savings_capacity']['comfortable'],0)
                self.assertTrue(any(c['category']=='Buy Now Pay Later' for c in ctx['financial_insights']['spending']['categories']))
                self.assertFalse(any(o['category']=='Buy Now Pay Later' for o in ctx['financial_insights']['savings_finder']))
                bank.create_goal(uid,Goal('funded','holiday',1000,1000,date(2028,11,15)),confirmed=True,expected_revision=1)
                goal=bank.store.read(uid,raw['as_of'])[1]['goals']['funded']
                self.assertEqual(goal['financial_assessment']['status'],'PASS');self.assertEqual(goal['contribution'],0)
                for target in (600,100000):
                    with self.assertRaises(GoalSafetyError):bank.create_goal(uid,Goal('new','holiday',target,0,date(2028,11,15)),confirmed=True,expected_revision=2)
                bank.trigger_presenter_event(uid,goal_id='funded',amount=1500,kind='unexpected_bill',confirmed=True,expected_revision=2)
                self.assertEqual(bank.store.read(uid,raw['as_of'])[1]['goals']['funded'],goal)
                self.assertEqual(raw,before)


class FundingApiTests(unittest.TestCase):
    def test_authenticated_scoped_designation_and_funded_creation(self):
        from api import create_app
        from fastapi.testclient import TestClient
        from tests.test_gemini_planner import MockGemini
        with TemporaryDirectory() as folder:
            app=create_app(Path(folder)/'api.sqlite',planner_transport=MockGemini())
            client=TestClient(app)
            payload={'account_id':'u003-savings','goal_available':1000.0,'protected':15000.0,
                     'reserves_confirmed':True,'confirmed':True,'expected_revision':0,'expected_financial_version':'unknown'}
            self.assertEqual(client.post('/api/savings-designations',json=payload).status_code,401)
            client.post('/api/session',json={'customer_id':'u003','demo_acknowledged':True})
            view=client.get('/api/overview').json();headers={'X-Context-Scope':view['scope_version']}
            payload.update(expected_revision=view['revision'],expected_financial_version=view['state_version'])
            response=client.post('/api/savings-designations',headers=headers,json={**payload,'account_id':'u001-savings'})
            self.assertEqual(response.status_code,400,response.text)
            self.assertEqual(client.post('/api/savings-designations',headers=headers,json={**payload,'reserves_confirmed':False}).status_code,400)
            response=client.post('/api/savings-designations',headers=headers,json=payload)
            self.assertEqual(response.status_code,200,response.text);view=response.json()
            self.assertEqual(view['goal_available'],1000)
            self.assertEqual(client.post('/api/savings-designations',headers=headers,json=payload).status_code,409)
            response=client.post('/api/goals',headers=headers,json={'kind':'holiday','target':1000.0,'allocated':1000.0,
                'deadline':'2028-11-15','confirmed':True,'expected_revision':view['revision']})
            self.assertEqual(response.status_code,200,response.text)
            goal=response.json()['goals'][0]
            self.assertEqual(goal['contribution'],0);self.assertTrue(goal['assessment_current'])
            self.assertEqual(goal['required_monthly'],0)


class FiniteCashFundingTests(unittest.TestCase):
    def make(self,balance=20000,deadline='2026-10-30',amount=100):
        ctx=deepcopy(demo_contexts()['normal']);f=ctx['financial_insights']
        f['balances'][0]['balance']=balance;f['cashflow']['runway']['balance']=balance
        f['cashflow']['runway']['projected_after_known_pending']=balance
        ctx['dated_cashflow_evidence']['starting_balance']=balance
        f['cashflow']['savings_capacity'].update(comfortable=0,stretch=0)
        ctx['funding_confirmation']={'reserves_confirmed':True}
        ctx['goals'][0].update(target=1300,deadline=deadline)
        tools=PlanningTools(ctx);calc=tools.calculate_strategy({'plans':[{'goal_ref':'goal-1','contribution':float(amount)}]})
        draft={'status':'draft_unverified','activation_allowed':False,'financial_version':ctx['state_version'],
               'selected_goal_ref':'goal-1','result':output(('calculation-1',)),'calculations':tools.calculations}
        return assess_draft(draft,ctx,tools.calculations,expected_goal_ref='goal-1'),calc,ctx,draft,tools
    def test_zero_guidance_can_pass_only_when_complete_finite_plan_is_cash_backed(self):
        assessment,calc,_,_,_=self.make()
        self.assertEqual(assessment['status'],'PASS')
        self.assertTrue(assessment['strategies'][0]['evidence']['finite_cash_funded_schedule'])
        self.assertTrue(calc['cash_funded_stress']['full_schedule_covered'])
        self.assertIn('Stress proof credits no future income.',calc['cash_funded_stress']['assumptions'])
        self.assertGreater(calc['cash_funded_stress']['minimum_cash'],200)
    def test_low_cash_unconfirmed_reserves_and_long_schedule_cannot_use_exception(self):
        a,_,_,_,_=self.make(balance=2000);self.assertNotEqual(a['status'],'PASS')
        _,calc,ctx,draft,tools=self.make();ctx['funding_confirmation']['reserves_confirmed']=False
        self.assertNotEqual(assess_draft(draft,ctx,tools.calculations,expected_goal_ref='goal-1')['status'],'PASS')
        a,_,_,_,_=self.make(deadline='2027-10-30',amount=10);self.assertNotEqual(a['status'],'PASS')
    def test_stress_proof_is_independently_verified(self):
        _,calc,ctx,draft,tools=self.make();calc['cash_funded_stress']['minimum_cash']+=100000
        self.assertEqual(assess_draft(draft,ctx,tools.calculations,expected_goal_ref='goal-1')['status'],'BLOCK')
