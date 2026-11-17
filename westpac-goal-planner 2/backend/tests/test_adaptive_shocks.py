"""Adaptive evidence, causal income monitoring, lifecycle isolation and labelled cases."""
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from finstats import Transaction
from finstats.shock_context import normalized_shocks, expectedness_for
from finstats.income_shocks import detect_income_interruptions
from planning.context import build_planning_context
from planning.schemas import PlannedExpense
from planning.service import BankingService
from planning.store import DemoStore, StaleState
from evaluation.shock_scenarios import scenarios, base_rows, ledger, END
from evaluation.run_shocks import evaluate


class AdaptiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases={c['name']:c for c in scenarios()}

    def events(self,name,**overrides):
        c=self.cases[name]
        return normalized_shocks(c['txns'],as_of=overrides.pop('as_of',c['as_of']),**{**c['kwargs'],**overrides})

    def target(self,name):
        return next(e for e in self.events(name) if 'target' in e['transaction_ids'])

    def test_unusualness_is_separate_from_financial_impact(self):
        low=self.target('cash_straining_purchase');rich=self.target('affordable_unusual_purchase')
        self.assertTrue(low['statistical_anomaly']);self.assertTrue(rich['statistical_anomaly'])
        self.assertEqual(low['potential_disruption'],'high')
        self.assertEqual(rich['potential_disruption'],'low')
        self.assertEqual(low['expectedness'],'unknown')
        self.assertFalse(low['confirmed_hardship'])
        self.assertLess(rich['impact']['liquidity_ratio'],low['impact']['liquidity_ratio'])
        self.assertIn('income_variability',low['impact'])
        self.assertIn('upcoming_obligations',low['impact'])

    def test_adaptive_floor_detects_a_small_customer_relative_shock(self):
        start=date(2026,1,1)
        rows=[Transaction(start+timedelta(days=i*8),'STORE '+chr(65+i),-5,'Shopping','expense',
                          account_id='a',transaction_id=str(i)) for i in range(10)]
        target=replace(rows[-1],date=start+timedelta(days=85),amount=-70,transaction_id='small-shock',description='NEW STORE')
        e=next(e for e in normalized_shocks(rows+[target],as_of=target.date) if 'small-shock' in e['transaction_ids'])
        self.assertLess(e['historical']['adaptive_floor'],200)
        self.assertTrue(e['statistical_anomaly'])

    def test_high_typical_amount_does_not_hide_a_robust_outlier(self):
        rows=[Transaction(date(2026,1,1)+timedelta(days=8*i),'NORMAL '+chr(65+i),-500,'Shopping','expense',
                          account_id='a',transaction_id=str(i)) for i in range(10)]
        target=replace(rows[-1],date=date(2026,4,1),description='LARGER PURCHASE',amount=-1000,transaction_id='target')
        event=next(e for e in normalized_shocks(rows+[target],as_of=target.date) if 'target' in e['transaction_ids'])
        self.assertIn('large_single_spend',event['detectors'])
        self.assertEqual(event['historical']['amount_vs_category_median'],2)

    def test_gradual_bill_increases_and_persistent_income_are_single_episodes(self):
        from finstats.pattern_shocks import detect_persistent_changes
        bills=[Transaction(date(2026,m,5),'UTILITY',-(100*1.04**(m-1)),'Utilities','expense',
                           True,True,account_id='a',transaction_id='b'+str(m)) for m in range(1,9)]
        early=detect_persistent_changes(bills,as_of=date(2026,6,30))
        late=detect_persistent_changes(bills,as_of=date(2026,8,31))
        a=next(e for e in early if e['event_type']=='recurring_cost_trend')
        b=next(e for e in late if e['event_type']=='recurring_cost_trend')
        self.assertEqual(a['event_ref'],b['event_ref'])
        incomes=[]
        for m in range(1,9):
            incomes += [Transaction(date(2026,m,1),'IRREGULAR CLIENT',4000 if m<=3 else 2000,'Income','income',
                                    transaction_id='i'+str(m)),
                        Transaction(date(2026,m,28 if m==2 else 30),'FOOD',-100,'Groceries','expense',transaction_id='s'+str(m))]
        a=next(e for e in detect_persistent_changes(incomes,as_of=date(2026,6,30)) if e['event_type']=='persistent_income_reduction')
        b=next(e for e in detect_persistent_changes(incomes,as_of=date(2026,8,31)) if e['event_type']=='persistent_income_reduction')
        self.assertEqual(a['event_ref'],b['event_ref'])


    def test_explicit_reserves_and_known_bills_affect_context_without_second_debit(self):
        from planning.schemas import AccountSnapshot, PendingExpense
        c=self.cases['affordable_unusual_purchase']
        accounts=(AccountSnapshot('cash','transaction',29500,END,protected=29000),)
        bill=PendingExpense('known',700,'Health',END,END)
        events=normalized_shocks(c['txns'],as_of=END,accounts=accounts,pending_expenses=(bill,))
        purchase=next(e for e in events if 'target' in e['transaction_ids'])
        self.assertEqual(purchase['impact']['current_balance'],29500)
        self.assertEqual(purchase['impact']['upcoming_obligations'],700)
        self.assertEqual(purchase['impact']['projected_balance'],28800)
        self.assertEqual(purchase['impact']['unreserved_projected_balance'],-200)
        self.assertEqual(purchase['potential_disruption'],'high')
        pending=next(e for e in events if e['status']=='outstanding')
        self.assertTrue(pending['impact']['already_in_projection'])
        self.assertEqual(pending['impact']['additional_future_debit'],700)
        self.assertEqual(purchase['impact']['additional_future_debit'],0)


    def test_low_history_is_uncertain_and_not_a_calibrated_probability(self):
        t=Transaction(date(2026,1,1),'SHOP',-10,'Shopping','expense',account_id='a',transaction_id='a')
        big=replace(t,date=date(2026,4,1),description='NEW',amount=-900,transaction_id='big')
        e=next(e for e in normalized_shocks([t,big],as_of=big.date) if 'big' in e['transaction_ids'])
        self.assertEqual(e['confidence'],'limited');self.assertFalse(e['statistical_anomaly'])
        self.assertEqual(e['expectedness'],'unknown')
        self.assertNotIn('probability',e)

    def test_planned_confirmed_and_unknown_are_different(self):
        self.assertEqual(self.target('planned_holiday')['expectedness'],'expected')
        self.assertEqual(self.target('emergency_vet')['expectedness'],'unexpected')
        self.assertEqual(self.target('affordable_unusual_purchase')['expectedness'],'unknown')
        c=self.cases['annual_insurance'];t=next(t for t in c['txns'] if t.transaction_id=='target')
        self.assertEqual(expectedness_for(t,[r for r in c['txns'] if r.date<t.date])[0],'expected')

    def test_explicit_plan_cannot_use_future_knowledge(self):
        c=self.cases['planned_holiday']
        plan=c['kwargs']['planned_expenses'][0]
        late=replace(plan,known_on=END+timedelta(days=1),due=END+timedelta(days=2))
        event=next(e for e in normalized_shocks(c['txns'],as_of=END,planned_expenses=(late,)) if 'target' in e['transaction_ids'])
        self.assertEqual(event['expectedness'],'unknown')

    def test_identity_does_not_depend_on_methods_or_current_analysis_date(self):
        c=self.cases['overlapping_detectors']
        first=next(e for e in normalized_shocks(c['txns'],as_of=END) if 'target' in e['transaction_ids'])
        earlier=replace(c['txns'][0],date=END-timedelta(days=20),description='ONE LARGE PURCHASE',
                        amount=-20,type='expense',category='Shopping',transaction_id='imported-history')
        changed=next(e for e in normalized_shocks(c['txns']+[earlier],as_of=END+timedelta(days=1)) if 'target' in e['transaction_ids'])
        self.assertEqual(first['event_ref'],changed['event_ref'])
        self.assertIn('new_merchant_big_amount',first['detectors'])
        score=next(e['score'] for e in first['evidence'] if e['detector']=='new_merchant_big_amount')
        self.assertAlmostEqual(score,first['event_amount']/first['historical']['adaptive_floor'])
        self.assertNotIn('new_merchant_big_amount',changed['detectors'])

    def test_distinct_transactions_same_amount_day_and_category_stay_distinct(self):
        c=self.cases['overlapping_detectors']
        t=next(t for t in c['txns'] if t.transaction_id=='target')
        twin=replace(t,transaction_id='other-target',description='ANOTHER BIG STORE')
        events=normalized_shocks(c['txns']+[twin],as_of=END)
        singles=[e for e in events if e['event_type']=='transaction_anomaly' and set(e['transaction_ids'])&{'target','other-target'}]
        self.assertEqual(len(singles),2)
        self.assertEqual(len({e['event_ref'] for e in singles}),2)

    def test_pattern_episode_grows_without_new_id_for_each_transaction(self):
        c=self.cases['persistent_essential_increase']
        early=normalized_shocks(c['txns'],as_of=END-timedelta(weeks=2))
        later=self.events('persistent_essential_increase')
        a=next(e for e in early if e['event_type']=='sustained_spending_change' and e['categories']==['Groceries'])
        b=next(e for e in later if e['event_type']=='sustained_spending_change' and e['categories']==['Groceries'])
        self.assertEqual(a['event_ref'],b['event_ref'])
        self.assertEqual(b['pattern_stage'],'sustained')
        self.assertFalse(any(e['event_type']=='transaction_anomaly' and e['categories']==['Groceries'] for e in later))
        self.assertIn('large_single_spend',b['detectors'])
        self.assertEqual(len([e for e in later if e['event_type']=='sustained_spending_change' and e['categories']==['Groceries']]),1)

    def test_a_separate_large_purchase_within_a_pattern_still_needs_review(self):
        c=self.cases['persistent_essential_increase']
        purchase=replace(c['txns'][-1],date=END-timedelta(days=2),amount=-2000,
            category='Groceries',description='UNUSUAL BULK PURCHASE',type='expense',account_id='cash',transaction_id='separate')
        events=normalized_shocks(ledger(c['txns']+[purchase],opening=30000),as_of=END)
        self.assertTrue(any(e['event_type']=='transaction_anomaly' and e['transaction_ids']==['separate'] for e in events))
        self.assertTrue(any(e['event_type']=='sustained_spending_change' for e in events))

    def test_replay_ignores_future_transactions_and_preserves_transfers(self):
        c=self.cases['missing_salary'];cutoff=END-timedelta(days=30)
        full=normalized_shocks(c['txns'],as_of=cutoff)
        truncated=normalized_shocks([t for t in c['txns'] if t.date<=cutoff],as_of=cutoff)
        self.assertEqual(full,truncated)
        self.assertFalse(any('target' in e['transaction_ids'] for e in self.events('internal_transfer')))

    def test_model_output_allowlist_and_stable_pseudonyms(self):
        c=self.cases['missing_salary']
        private=[replace(t,account_id='SECRET_ACCOUNT',user_id='SECRET_CUSTOMER',description=t.description+' SECRET_NAME') for t in c['txns']]
        ctx=build_planning_context(private,as_of=c['as_of'])
        encoded=json.dumps(ctx,allow_nan=False)
        self.assertNotIn('SECRET',encoded)
        self.assertNotIn('transaction_ids',encoded)
        self.assertNotIn('_income_source',encoded)
        self.assertEqual(ctx['schema_version'],'1.2')
        event=next(e for e in ctx['shocks'] if e['event_type']=='missing_income')
        self.assertEqual(event['status'],'income_missing')
        self.assertIn('grace_end',event['historical'])
        self.assertIn('potential_disruption',event)

    def test_old_individual_event_insight_feedback_remains_recognized(self):
        from hashlib import sha256
        from finstats.banking import banking_insights
        c=self.cases['emergency_vet']
        view=banking_insights(c['txns'],as_of=END,event_evidence=c['kwargs']['event_evidence'])
        event=next(e for e in view['shocks'] if 'target' in e['transaction_ids'])
        legacy_ref=sha256((event['date']+str(sorted(event['transaction_ids']))+str(sorted(event['detectors']))).encode()).hexdigest()[:16]
        old_insight=sha256(('unusual_expense'+legacy_ref).encode()).hexdigest()[:24]
        after=banking_insights(c['txns'],as_of=END,event_evidence=c['kwargs']['event_evidence'],
                              feedback=({'action':'dismiss','insight_id':old_insight},))
        self.assertFalse(any(i['type']=='unusual_expense' and i['values']['event_amount']==1850 for i in after['personalised_insights']))


    def test_labelled_evaluation_records_precision_recall_and_all_outcomes(self):
        report=evaluate()
        self.assertEqual(len(report['scenarios']),14)
        self.assertEqual((report['false_positives'],report['false_negatives']),(0,0))
        self.assertTrue(report['not_real_world_accuracy'])
        self.assertEqual((report['precision'],report['recall']),(1,1))
        self.assertTrue(all(r['expectedness_label'] is None or r['expectedness_label']==r['expectedness_observed'] for r in report['scenarios']))


class IncomeTests(unittest.TestCase):
    def test_no_missing_before_grace_and_late_arrival_reuses_gap_identity(self):
        cases={c['name']:c for c in scenarios()};base=cases['missing_salary']['txns']
        self.assertFalse(detect_income_interruptions(base,as_of=END+timedelta(days=3)))
        missing=detect_income_interruptions(base,as_of=END+timedelta(days=4))[0]
        late=replace(base[0],date=END+timedelta(days=5),amount=1000,transaction_id='late')
        delivered=detect_income_interruptions(base+[late],as_of=late.date)[0]
        self.assertEqual(missing['event_ref'],delivered['event_ref'])
        self.assertEqual(delivered['event_type'],'delayed_income')
        self.assertTrue(delivered['impact']['already_in_balance'])

    def test_month_end_weekend_and_known_holiday_grace(self):
        rows=[Transaction(date(2026,m,d),'PAYROLL',2000,'Income','income',account_id='a',transaction_id=str(m))
              for m,d in ((1,31),(2,28),(3,31),(4,30))]
        self.assertFalse(detect_income_interruptions(rows,as_of=date(2026,6,3)))
        missing=detect_income_interruptions(rows,as_of=date(2026,6,4))[0]
        self.assertEqual(missing['historical']['expected_date'],'2026-05-31')
        self.assertFalse(detect_income_interruptions(rows,as_of=date(2026,6,4),known_holidays=(date(2026,6,1),)))

    def test_weekly_fortnightly_monthly_multiple_sources_and_irregular_invoices(self):
        for period in (7,14):
            rows=[Transaction(date(2026,1,5)+timedelta(days=period*i),'PAYROLL',1000,'Income','income',account_id='a',transaction_id=str(i)) for i in range(4)]
            other=[replace(t,description='SECOND SOURCE',account_id='b',transaction_id='b'+t.transaction_id) for t in rows]
            asof=rows[-1].date+timedelta(days=period+5)
            events=detect_income_interruptions(rows+other,as_of=asof)
            self.assertEqual(len(events),2)
            self.assertEqual(len({e['event_ref'] for e in events}),2)
        c=next(c for c in scenarios() if c['name']=='irregular_freelance')
        self.assertFalse(detect_income_interruptions(c['txns'],as_of=c['as_of']))

    def test_reduced_and_persistent_reduced_pay_are_one_episode(self):
        rows=[Transaction(date(2026,1,5)+timedelta(weeks=i),'PAYROLL',1000 if i<4 else 500,
                          'Income','income',account_id='a',transaction_id=str(i)) for i in range(7)]
        events=detect_income_interruptions(rows,as_of=rows[-1].date)
        reduced=[e for e in events if e['event_type']=='reduced_income']
        self.assertEqual(len(reduced),1)
        self.assertEqual(reduced[0]['pattern_stage'],'sustained')
        self.assertEqual(reduced[0]['historical']['shortfall'],500)
        self.assertEqual(reduced[0]['impact']['additional_future_debit'],0)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'state.sqlite'
        self.store=DemoStore(self.path)
        self.case=next(c for c in scenarios() if c['name']=='cash_straining_purchase')
        self.raw={'txns':self.case['txns'],'as_of':END}
        self.service=BankingService(self.store,lambda c:self.raw)
        self.customer='evaluation'

    def target(self):
        return next(e for e in self.service.banking_view(self.customer)['insights']['shocks'] if 'target' in e['transaction_ids'])

    def action(self,action,event=None,**kw):
        e=event or self.target()
        revision=self.store.read(self.customer,self.raw['as_of'])[0]
        return self.service.record_shock_action(self.customer,e['event_ref'],action=action,evidence_version=e['evidence_version'],
            confirmed=True,expected_revision=revision,**kw)

    def test_confirm_acknowledge_dismiss_resolve_and_reopen_persist(self):
        self.action('confirm_expected',remember_expected=True)
        e=self.target();self.assertEqual(e['expectedness'],'expected')
        self.assertFalse(e['notification_required'])
        for action,status in [('acknowledge','acknowledged'),('dismiss','dismissed'),('resolve','resolved'),('reopen','new')]:
            self.action(action)
            self.assertEqual(self.target()['lifecycle_status'],status)
        reopened=BankingService(DemoStore(self.path),lambda c:self.raw)
        history=reopened.shock_history(self.customer)
        self.assertEqual(len(history[0]['history']),5)
        self.assertEqual(history[0]['expectedness'],'expected')

    def test_acknowledgement_does_not_invent_an_expectedness_confirmation(self):
        self.raw['planned_expenses']=(PlannedExpense('planned','target',500,'Shopping',END-timedelta(days=1),END),)
        before=self.target()
        self.assertEqual(before['expectedness_source'],'explicit_plan')
        self.action('acknowledge')
        after=self.target()
        self.assertEqual(after['lifecycle_status'],'acknowledged')
        self.assertEqual(after['expectedness'],'expected')
        self.assertEqual(after['expectedness_source'],'explicit_plan')
        self.assertEqual(after['user_confirmation'],'none')

    def test_unexpected_confirmation_and_future_feedback_isolation(self):
        self.action('confirm_unexpected')
        self.assertTrue(self.target()['unexpected_confirmed'])
        self.assertEqual(self.store.read('other-customer',END)[1]['shock_events'],{})
        self.assertEqual(self.store.read(self.customer,END-timedelta(days=1))[1]['shock_events'],{})

    def test_stale_evidence_and_changed_financial_conditions_require_review(self):
        old=self.target();self.action('dismiss',old)
        t=next(t for t in self.raw['txns'] if t.transaction_id=='target')
        self.raw['txns']=[replace(r,amount=-2000,balance_after=-1400) if r.transaction_id=='target' else r for r in self.raw['txns']]
        with self.assertRaises(StaleState):
            self.action('confirm_expected',old)
        current=self.target()
        self.assertEqual(current['event_ref'],old['event_ref'])
        self.assertTrue(current['reassessment_required'])
        self.assertTrue(current['notification_required'])
        self.assertEqual(current['lifecycle_status'],'new')

    def test_remembered_annual_expectation_does_not_suppress_new_cash_risk(self):
        e=self.target();self.action('confirm_expected',e,remember_expected=True)
        original=next(t for t in self.raw['txns'] if t.transaction_id=='target')
        later=replace(original,date=END+timedelta(days=365),transaction_id='annual-next',balance_after=-400)
        recent=[replace(original,date=later.date-timedelta(days=i*2),transaction_id='recent'+str(i),amount=-20,
                        description='RECENT '+chr(65+i)) for i in range(1,10)]
        self.raw={'txns':ledger(base_rows()+[original]+recent+[later],opening=600),'as_of':later.date}
        new=next(e for e in self.service.banking_view(self.customer)['insights']['shocks'] if 'annual-next' in e['transaction_ids'])
        self.assertEqual(new['expectedness'],'expected')
        self.assertEqual(new['potential_disruption'],'high')
        self.assertTrue(new['notification_required'])
