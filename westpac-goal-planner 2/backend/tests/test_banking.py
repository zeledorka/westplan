"""End-to-end banking contracts, persisted decisions and trusted financial state."""
from dataclasses import replace
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from finstats import Transaction, load_transactions, savings_capacity
from finstats.banking import banking_insights
from finstats.budgets import BudgetLimit, budget_summary
from finstats.bills import bills_calendar, bill_insights
from finstats.categorisation import classify_transaction, classified_transactions
from finstats.search import parse_question, search_transactions
from planning.context import build_planning_context, context_json
from planning.demo import AS_OF, demo_transactions, demo_contexts
from planning.schemas import AccountSnapshot, Goal, PendingExpense
from planning.service import BankingService
from planning.store import DemoStore, StaleState


def fixture_inputs(customer='synthetic-holiday', paid=False):
    return dict(txns=[replace(t,user_id=customer) for t in demo_transactions(paid)],as_of=AS_OF,
        accounts=(AccountSnapshot('demo-everyday','transaction',2000,date(2026,1,1)),
                  AccountSnapshot('demo-savings','savings',9300,AS_OF,8100,1200)))


class BankingAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tx=demo_transactions()
        cls.normal=banking_insights(cls.tx,as_of=AS_OF)

    def test_budget_actual_limits_and_protection(self):
        snapshot=budget_summary(self.tx,as_of=AS_OF,
            limits=(BudgetLimit('Eating out',350,True),BudgetLimit('Entertainment',150)))
        rows={r['category']:r for r in snapshot['categories']}
        self.assertEqual(rows['Eating out']['spent'],300)
        self.assertEqual(rows['Eating out']['remaining'],50)
        self.assertEqual(rows['Eating out']['status'],'approaching_limit')
        self.assertTrue(rows['Eating out']['protected'])
        self.assertFalse(rows['Eating out']['remaining_is_cash_available'])
        self.assertEqual(rows['Entertainment']['status'],'over_budget')
        self.assertEqual(rows['Entertainment']['remaining'],-30)
        self.assertEqual(rows['Housing']['source'],'unset')
        self.assertIsNone(rows['Housing']['monthly_limit'])
        self.assertNotIn('Transfers',rows)
        self.assertEqual(rows['Housing']['essential_spent'],1600)

    def test_budget_refunds_future_transactions_and_provenance(self):
        refund=replace(self.tx[-1],date=AS_OF,amount=50,type='expense',category='Eating out')
        future=replace(refund,date=date(2026,10,1),amount=-9999)
        rows=budget_summary(self.tx+[refund,future],as_of=AS_OF,
            limits=(BudgetLimit('Eating out',250,source='demo_default'),))['categories']
        row=next(r for r in rows if r['category']=='Eating out')
        self.assertEqual(row['spent'],250)
        self.assertEqual(row['source'],'demo_default')
        with self.assertRaises(ValueError):
            budget_summary(self.tx,as_of=AS_OF,limits=(BudgetLimit('Shopping',1),BudgetLimit('Shopping',2)))
        with self.assertRaises(ValueError):
            BudgetLimit('Housing',float('inf'))

    def test_bills_calendar_paid_future_and_transfers(self):
        cal=self.normal['bills_calendar']
        self.assertEqual(len({b['bill_id'] for b in cal['items']}),len(cal['items']))
        self.assertTrue(all(b['category']!='Transfers' for b in cal['items']))
        paid=[b for b in cal['items'] if b['already_paid']]
        self.assertTrue(paid)
        self.assertTrue(all(b['expected_date']<=AS_OF.isoformat() for b in paid))
        upcoming=[b for b in cal['items'] if b['status']=='upcoming']
        self.assertTrue(all(b['expected_date']>AS_OF.isoformat() and b['date_kind']=='estimated' for b in upcoming))
        self.assertTrue(all(not b['confirmed'] for b in upcoming))
        for days,summary in cal['summary_days'].items():
            end=date.fromordinal(AS_OF.toordinal()+int(days)).isoformat()
            self.assertEqual(summary['total_expected'],sum(b['expected_amount'] for b in upcoming if b['expected_date']<=end))

    def test_one_off_paid_unpaid_anticipated_and_replacement(self):
        p=PendingExpense('vet',1850,'Pets',AS_OF,AS_OF)
        cal=bills_calendar(self.tx,as_of=AS_OF,pending_expenses=(p,))
        self.assertEqual(cal['summary_days']['7']['confirmed_due'],1850)
        uncertain=bills_calendar(self.tx,as_of=AS_OF,pending_expenses=(replace(p,status='anticipated'),))
        self.assertEqual(uncertain['summary_days']['7']['confirmed_due'],0)
        self.assertEqual(uncertain['summary_days']['7']['anticipated'],1850)
        paid=demo_transactions(True)
        linked=replace(p,transaction_id=paid[-1].transaction_id)
        self.assertFalse(any(b['bill_id']=='pending-vet' for b in bills_calendar(paid,as_of=AS_OF,pending_expenses=(linked,))['items']))
        bill=next(b for b in cal['items'] if b['status']=='upcoming' and b['category']=='Subscriptions')
        corrected=PendingExpense('confirmed-subscription',25,'Subscriptions',AS_OF,date.fromisoformat(bill['expected_date']),replaces_bill_id=bill['bill_id'])
        replaced=bills_calendar(self.tx,as_of=AS_OF,pending_expenses=(corrected,))
        self.assertNotIn(bill['bill_id'],{b['bill_id'] for b in replaced['items']})
        self.assertEqual(replaced['summary_days']['30']['total_expected'],cal['summary_days']['30']['total_expected']-1850-20+25)
        with self.assertRaises(ValueError):
            bills_calendar(self.tx,as_of=AS_OF,pending_expenses=(p,p))

    def test_historical_calendar_does_not_use_future_recurrence(self):
        cutoff=date(2026,2,15)
        a=bills_calendar(self.tx,as_of=cutoff)
        b=bills_calendar([t for t in self.tx if t.date<=cutoff],as_of=cutoff)
        self.assertEqual(a,b)
        self.assertFalse(any(x['category']=='Subscriptions' for x in a['items']))

    def test_finder_ranges_protected_essential_and_no_goal_strategy(self):
        normal=banking_insights(self.tx,as_of=AS_OF,protected_categories=('Eating out',))
        opportunities=normal['savings_finder']
        self.assertFalse(any(o['category'] in {'Housing','Groceries','Eating out'} for o in opportunities))
        recurring=next(o for o in opportunities if o['kind']=='recurring_discretionary_review' and o['category']=='Subscriptions')
        self.assertEqual(recurring['monthly_savings_range']['minimum'],0)
        self.assertAlmostEqual(recurring['monthly_savings_range']['maximum'],20*365.25/12/recurring['evidence']['period_days'],places=2)
        self.assertTrue(recurring['requires_confirmation'])
        self.assertEqual(len({o['opportunity_id'] for o in opportunities}),len(opportunities))
        self.assertTrue(all(o['monthly_savings_range']['minimum']<=o['monthly_savings_range']['maximum'] for o in opportunities))
        self.assertNotIn('strategy',normal)

    def test_subscription_increase_and_possible_overlap_evidence(self):
        tx=[Transaction(date(2026,m,5),'SPOTIFY',-v,'Subscriptions','expense',
                        account_id='a',user_id='u',transaction_id=f'a{m}')
            for m,v in [(1,20),(2,20),(3,20),(4,30)]]
        tx += [replace(t,description='NETFLIX.COM',transaction_id='n'+t.transaction_id,amount=-15) for t in tx]
        view=banking_insights(tx,as_of=date(2026,4,30))
        kinds={o['kind'] for o in view['savings_finder']}
        self.assertIn('recurring_cost_increase',kinds)
        self.assertIn('possible_service_overlap',kinds)
        forecast=next(b for b in view['bills_calendar']['items'] if b['merchant']=='SPOTIFY' and b['status']=='upcoming')
        self.assertEqual(forecast['expected_amount'],30)
        self.assertEqual(forecast['amount_basis'],'latest_detected_price_change')
        self.assertTrue(all(not o['evidence'].get('overlap_confirmed',False) for o in view['savings_finder']))
        # A price change is one consolidated insight, even if multiple detectors fired.
        ids=[i['insight_id'] for i in view['personalised_insights']]
        self.assertEqual(len(ids),len(set(ids)))

    def test_personalised_risk_dedup_and_feedback(self):
        paid=banking_insights(demo_transactions(True),as_of=AS_OF)
        risk=[i for i in paid['personalised_insights'] if i['type']=='upcoming_cashflow_risk']
        self.assertEqual(len(risk),1)
        self.assertEqual(risk[0]['values']['projected'],150)
        feedback=({'action':'dismiss','insight_id':risk[0]['insight_id']},)
        after=banking_insights(demo_transactions(True),as_of=AS_OF,feedback=feedback)
        self.assertIn(risk[0],after['personalised_insights'])
        event=[i for i in paid['personalised_insights'] if i['type']=='unusual_expense' and i['values']['event_amount']==1850]
        self.assertEqual(len(event),1)
        info=next(i for i in self.normal['personalised_insights'] if i['severity']=='info')
        hidden=banking_insights(self.tx,as_of=AS_OF,feedback=({'action':'not_useful','insight_id':info['insight_id']},))
        self.assertNotIn(info['insight_id'],{i['insight_id'] for i in hidden['personalised_insights']})

    def test_security_notifications_survive_disliked_feedback(self):
        rows=[Transaction(date(2026,1,d),'SHOP',-10,'Shopping','expense',transaction_id=str(d)) for d in range(1,15)]
        rows += [Transaction(date(2026,1,15),'SHOP',-10,'Shopping','expense',transaction_id='burst'+str(i)) for i in range(10)]
        view=banking_insights(rows,as_of=date(2026,1,15))
        alert=next(i for i in view['personalised_insights'] if i['type']=='security_review')
        again=banking_insights(rows,as_of=date(2026,1,15),feedback=({'action':'not_useful','insight_id':alert['insight_id']},))
        self.assertIn(alert,again['personalised_insights'])


    def test_extended_context_privacy_and_scenarios(self):
        contexts=demo_contexts()
        self.assertEqual(contexts['normal']['financial_insights']['cashflow']['savings_capacity']['comfortable'],900)
        self.assertEqual(contexts['vet_paid']['financial_insights']['cashflow']['savings_capacity']['comfortable'],490.73)
        self.assertEqual(contexts['vet_unpaid']['financial_insights']['bills_calendar']['summary_days']['7']['confirmed_due'],1850)
        secret=[replace(t,description='SECRET_NAME_ADDRESS',category='SECRET_CATEGORY',account_id='SECRET_ACCOUNT') for t in self.tx]
        c=build_planning_context(secret,as_of=AS_OF,budget_limits=(BudgetLimit('Eating out',250),))
        text=context_json(c)
        self.assertNotIn('SECRET',text)
        self.assertNotIn('merchant',text)
        self.assertNotIn('subscription_id',text)
        self.assertIn('savings_finder',c['financial_insights'])
        self.assertEqual(json.loads(text),c)

    def test_all_five_personas_actuals_and_json_consistency(self):
        data=Path(__file__).resolve().parent.parent/'data/mock_transactions.json'
        results=[]
        for uid in ('u001','u002','u003','u004','u005'):
            with self.subTest(uid=uid):
                tx=load_transactions(data,uid)
                before=list(tx)
                ctx=build_planning_context(tx,as_of=date(2026,11,15))
                i=ctx['financial_insights']
                self.assertEqual(i['cashflow']['savings_capacity']['comfortable'],savings_capacity(tx).comfortable)
                spent=sum(b['spent'] for b in i['budgets']['categories'])
                expected=round(sum(-t.amount for t in tx if t.is_expense and t.date>=date(2026,11,1)),2)
                self.assertAlmostEqual(spent,expected,places=2)
                self.assertEqual(before,tx)
                json.dumps(ctx,allow_nan=False)
                results.append(i['spending']['monthly_discretionary'])
        self.assertEqual(len(set(results)),5)


class CategorisationTests(unittest.TestCase):
    def test_known_merchant_unknown_and_explicit_preservation(self):
        t=Transaction(AS_OF,'WOOLWORTHS NEWTOWN',-50,'','expense')
        c=classify_transaction(t)
        self.assertEqual((c.category,c.essential,c.source),('Groceries',True,'merchant_rule'))
        explicit=replace(t,category='Shopping')
        self.assertEqual(classify_transaction(explicit).category,'Shopping')
        self.assertEqual(classify_transaction(replace(t,description='AMAZON AU')).source,'uncertain')
        self.assertIsNone(classify_transaction(replace(t,description='UNKNOWN SHOP')).essential)
        self.assertTrue(classified_transactions([replace(t,description='UNKNOWN SHOP')])[0].essential)

    def test_largest_expenses_and_subscription_search_templates(self):
        tx=demo_transactions(True)
        query=parse_question('What were my largest expenses this year?',as_of=AS_OF)
        result=search_transactions(tx,query,as_of=AS_OF)
        self.assertEqual(result['transactions'][0]['amount'],1850)
        bills=[Transaction(date(2026,m,5),'SPOTIFY',-v,'Subscriptions','expense',transaction_id=str(m))
               for m,v in [(1,20),(2,20),(3,20),(4,30)]]
        result=search_transactions(bills,parse_question('Which subscriptions increased in price?',as_of=AS_OF),as_of=AS_OF)
        self.assertEqual(result['changes'],[{'date':'2026-04-05','previous':20,'current':30}])

    def test_rules_against_existing_synthetic_merchants(self):
        tx=load_transactions(Path(__file__).resolve().parent.parent/'data/mock_transactions.json','u003')
        tested=0
        for t in tx:
            if t.is_expense:
                c=classify_transaction(replace(t,category=''))
                if c.source=='merchant_rule':
                    self.assertEqual(c.category,t.category)
                    tested+=1
        self.assertGreater(tested,100)


class PersistenceAndApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'demo.sqlite'
        self.store=DemoStore(self.path)
        self.raw=fixture_inputs()
        self.service=BankingService(self.store,lambda c: self.raw if c=='synthetic-holiday' else fixture_inputs(c))
        self.customer='synthetic-holiday'

    def revision(self):
        return self.store.read(self.customer,self.raw['as_of'])[0]

    def goal(self):
        g=Goal('holiday','holiday',6000,1200,date(2027,5,31))
        self.service.create_goal(self.customer,g,confirmed=True,expected_revision=self.revision())

    def test_budget_confirmation_and_atomic_stale_writes(self):
        with self.assertRaises(ValueError):
            self.service.set_budget(self.customer,BudgetLimit('Eating out',250),confirmed=False,expected_revision=0)
        self.assertEqual(self.revision(),0)
        self.service.set_budget(self.customer,BudgetLimit('Eating out',250,True),confirmed=True,expected_revision=0)
        with self.assertRaises(StaleState):
            self.service.set_budget(self.customer,BudgetLimit('Shopping',100),confirmed=True,expected_revision=0)
        persisted=DemoStore(self.path).read(self.customer,AS_OF)[1]
        self.assertEqual(persisted['budgets']['Eating out']['monthly_limit'],250)
        self.assertNotIn('Shopping',persisted['budgets'])
        context=self.service.planning_context(self.customer)
        self.assertIn('Eating out',context['preferences']['protected_categories'])

    def test_manual_corrections_persist_and_are_scoped(self):
        row=self.raw['txns'][3]
        self.service.correct_category(self.customer,row.transaction_id,'Shopping',False,confirmed=True,expected_revision=0)
        corrections=DemoStore(self.path).read(self.customer,AS_OF)[1]
        c=classify_transaction(replace(row,category=''),merchant_corrections=corrections['corrections'])
        self.assertEqual(c.category,'Shopping')
        self.assertEqual(c.source,'manual_merchant')
        self.assertEqual(self.store.read('another',AS_OF)[1]['corrections'],{})
        with self.assertRaises(ValueError):
            self.service.correct_category(self.customer,'unknown-id','Shopping',False,confirmed=True,expected_revision=1)
        self.assertEqual(row,self.raw['txns'][3])

    def test_feedback_preference_persistence_history_and_scope(self):
        for marker in ('proposal-1','proposal-2'):
            self.service.record_feedback(self.customer,action='reject_adjustment',category='Eating out',insight_id=marker,
                                         confirmed=True,expected_revision=self.revision())
        # Same retry is not a third rejection.
        self.service.record_feedback(self.customer,action='reject_adjustment',category='Eating out',insight_id='proposal-2',
                                     confirmed=True,expected_revision=self.revision())
        c=self.service.planning_context(self.customer)
        self.assertEqual(c['preferences']['unwanted_reduction_categories'],['Eating out'])
        self.assertEqual(c['preferences']['feedback_summary']['reject_adjustment'],2)
        self.service.record_feedback(self.customer,action='protect_category',category='Entertainment',
                                     confirmed=True,expected_revision=self.revision())
        reopened=BankingService(DemoStore(self.path),lambda c:self.raw)
        self.assertIn('Entertainment',reopened.planning_context(self.customer)['preferences']['protected_categories'])
        self.assertEqual(self.store.read(self.customer,date(2026,9,29))[1]['feedback'],[])
        self.assertEqual(self.store.read('other',AS_OF)[1]['feedback'],[])

    def test_feedback_useful_restores_noncritical_insight_and_cannot_approve_goal(self):
        view=self.service.banking_view(self.customer)
        insight=next(i for i in view['insights']['personalised_insights'] if i['severity']=='info')
        for action in ('dismiss','useful'):
            self.service.record_feedback(self.customer,action=action,insight_id=insight['insight_id'],
                                         confirmed=True,expected_revision=self.revision())
        later=self.service.banking_view(self.customer)
        self.assertIn(insight['insight_id'],{i['insight_id'] for i in later['insights']['personalised_insights']})
        self.assertEqual(self.store.read(self.customer,AS_OF)[1]['goals'],{})

    def test_subscription_cancelled_state_does_not_leak_to_past(self):
        sub=self.service.subscriptions(self.customer)[0]
        for action in ('request_cancellation','confirm_cancelled'):
            self.service.subscription_action(self.customer,sub['subscription_id'],action,confirmed=True,expected_revision=self.revision())
        self.assertEqual(self.store.read(self.customer,date(2026,9,29))[1]['subscriptions'],{})
        self.assertEqual(self.store.read('other',AS_OF)[1]['subscriptions'],{})

    def test_preference_and_other_goal_changes_invalidate_pending_proposal(self):
        self.goal()
        first=self.service.propose_goal_change(self.customer,'holiday',{'target':6500},expected_revision=self.revision())
        second=self.service.propose_goal_change(self.customer,'holiday',{'deadline':'2027-08-31'},expected_revision=self.revision())
        self.service.decide_proposal(self.customer,second['proposal_id'],'approve',confirmed=True,expected_revision=self.revision())
        with self.assertRaises(StaleState):
            self.service.decide_proposal(self.customer,first['proposal_id'],'approve',confirmed=True,expected_revision=self.revision())
        third=self.service.propose_goal_change(self.customer,'holiday',{'target':7000},expected_revision=self.revision())
        self.service.record_feedback(self.customer,action='protect_category',category='Eating out',confirmed=True,expected_revision=self.revision())
        with self.assertRaises(StaleState):
            self.service.decide_proposal(self.customer,third['proposal_id'],'approve',confirmed=True,expected_revision=self.revision())


    def test_goal_snooze_approve_reject_and_versions(self):
        self.goal()
        p=self.service.propose_goal_change(self.customer,'holiday',{'target':6500},expected_revision=self.revision())
        self.service.decide_proposal(self.customer,p['proposal_id'],'snooze',confirmed=True,expected_revision=self.revision())
        self.assertEqual(self.store.read(self.customer,AS_OF)[1]['goals']['holiday']['target'],6000)
        self.service.decide_proposal(self.customer,p['proposal_id'],'approve',confirmed=True,expected_revision=self.revision())
        g=self.store.read(self.customer,AS_OF)[1]['goals']['holiday']
        self.assertEqual((g['target'],g['version']),(6500,2))
        p=self.service.propose_goal_change(self.customer,'holiday',{'deadline':'2027-08-31'},expected_revision=self.revision())
        self.service.decide_proposal(self.customer,p['proposal_id'],'reject',confirmed=True,expected_revision=self.revision())
        self.assertEqual(self.store.read(self.customer,AS_OF)[1]['goals']['holiday']['deadline'],'2027-05-31')

    def test_stale_financial_goal_or_preference_proposal_cannot_apply(self):
        self.goal()
        p=self.service.propose_goal_change(self.customer,'holiday',{'target':6500},expected_revision=self.revision())
        self.raw['txns']=fixture_inputs(paid=True)['txns']
        with self.assertRaises(StaleState):
            self.service.decide_proposal(self.customer,p['proposal_id'],'approve',confirmed=True,expected_revision=self.revision())
        self.assertEqual(self.store.read(self.customer,AS_OF)[1]['proposals'][p['proposal_id']]['status'],'pending')
        self.assertEqual(self.store.read(self.customer,AS_OF)[1]['goals']['holiday']['target'],6000)

    def test_invalid_goal_allocation_and_ai_validation_cannot_be_fabricated(self):
        self.goal()
        p=self.service.propose_goal_change(self.customer,'holiday',{'allocated':5000},expected_revision=self.revision())
        with self.assertRaises(ValueError):
            self.service.decide_proposal(self.customer,p['proposal_id'],'approve',confirmed=True,expected_revision=self.revision())
        with self.assertRaises(ValueError):
            self.service.propose_goal_change(self.customer,'holiday',{'target':6500},source='planner',expected_revision=self.revision())
        seen=[]
        trusted=BankingService(self.store,lambda c:self.raw,proposal_validator=lambda p,c: seen.append(c['state_version']) or True)
        proposal=trusted.propose_goal_change(self.customer,'holiday',{'target':6500},source='planner',expected_revision=self.revision())
        trusted.decide_proposal(self.customer,proposal['proposal_id'],'approve',confirmed=True,expected_revision=self.revision())
        self.assertEqual(len(seen),2)  # validate again at approval, using server-rebuilt context
        self.assertEqual(self.store.read(self.customer,AS_OF)[1]['goals']['holiday']['target'],6500)

    def test_subscription_request_failure_retry_and_confirmed_projection(self):
        sub=self.service.subscriptions(self.customer)[0]
        sid=sub['subscription_id']
        baseline=self.service.planning_context(self.customer)
        due=baseline['financial_insights']['bills_calendar']['summary_days']['30']['total_expected']
        def action(name):
            self.service.subscription_action(self.customer,sid,name,confirmed=True,expected_revision=self.revision())
        action('request_cancellation')
        self.assertEqual(self.service.subscriptions(self.customer)[0]['status'],'cancellation_requested')
        self.assertEqual(self.service.planning_context(self.customer)['financial_insights']['bills_calendar']['summary_days']['30']['total_expected'],due)
        action('confirm_failed')
        self.assertEqual(self.service.subscriptions(self.customer)[0]['status'],'cancellation_failed')
        action('request_cancellation');action('confirm_cancelled')
        after=self.service.planning_context(self.customer)
        self.assertEqual(after['financial_insights']['bills_calendar']['summary_days']['30']['total_expected'],due-20)
        self.assertEqual(after['financial_insights']['cashflow']['savings_capacity'],baseline['financial_insights']['cashflow']['savings_capacity'])
        self.assertEqual(after['financial_insights']['balances'],baseline['financial_insights']['balances'])
        self.assertEqual(len(self.raw['txns']),len(demo_transactions()))
        with self.assertRaises(ValueError):
            action('request_cancellation')

    def test_read_only_search_and_customer_provider_validation(self):
        result=self.service.search(self.customer,'How much did I spend on groceries last month?')
        self.assertEqual(result['total_spent'],500)
        self.assertEqual((result['start'],result['end']),('2026-08-01','2026-08-31'))
        three=self.service.search(self.customer,'How much did I spend on transport over the last three months?')
        self.assertEqual(three['total_spent'],600)
        self.assertEqual(self.revision(),0)
        with self.assertRaises(ValueError):
            self.service.search(self.customer,'DROP TABLE transactions')
        with self.assertRaises(ValueError):
            BankingService(self.store,lambda c:self.raw).planning_context('another-customer')
