"""Behavioral contracts for financial facts, causal replay and the model boundary."""
import json
import math
import unittest
from dataclasses import replace
from datetime import date
from finstats import Transaction, savings_capacity
from finstats.insights import financial_insights
from finstats.bills import bill_insights
from finstats.shock_context import normalized_shocks
from planning.context import build_planning_context, context_json
from planning.schemas import AccountSnapshot, Goal, Preferences, PendingExpense, PostedEventEvidence
from planning.calculation_tools import contribution_projection, required_contribution, contribution_cashflow
from planning.demo import demo_contexts, demo_transactions, AS_OF


class FoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contexts = demo_contexts()
        cls.normal = cls.contexts['normal']
        cls.paid = cls.contexts['vet_paid']

    def test_income_spending_and_transfers(self):
        i = self.normal['financial_insights']
        self.assertEqual(i['observation']['complete_months'], 9)
        self.assertEqual(i['income']['monthly_average'], 4000)
        self.assertEqual(i['income']['regular_monthly_estimate'], 4000)
        self.assertEqual(i['spending']['monthly_essential'], 2600)
        self.assertEqual(i['spending']['monthly_discretionary'], 500)
        self.assertEqual(i['cashflow']['average_surplus'], 900)
        self.assertEqual(i['cashflow']['savings_capacity']['comfortable'], 900)
        # Existing savings API and new interface agree, including posted event.
        for paid in (False, True):
            tx = demo_transactions(paid)
            actual = financial_insights(tx, as_of=AS_OF)['cashflow']['savings_capacity']
            self.assertEqual(actual['comfortable'], savings_capacity(tx).comfortable)

    def test_balances_reconcile_to_ledger(self):
        for paid in (False, True):
            tx = demo_transactions(paid)
            opening = {'demo-everyday': 2000, 'demo-savings': 1200}
            for t in tx:
                opening[t.account_id] = round(opening[t.account_id] + t.amount, 2)
                self.assertEqual(opening[t.account_id], t.balance_after)
            self.assertEqual(opening['demo-everyday'], 150 if paid else 2000)
            self.assertEqual(opening['demo-savings'], 9300)

    def test_event_changes_capacity_and_cash_without_double_debit(self):
        before = self.normal['financial_insights']['cashflow']
        after = self.paid['financial_insights']['cashflow']
        self.assertEqual(before['runway']['balance'] - after['runway']['balance'], 1850)
        self.assertEqual(after['runway']['projected_after_known_pending'], 150)
        self.assertLess(after['savings_capacity']['comfortable'], 600)
        event = next(s for s in self.paid['shocks'] if s['event_amount'] == 1850)
        self.assertEqual(event['status'], 'posted')
        self.assertFalse(event['confirmed_hardship'])
        self.assertTrue(event['unexpected_confirmed'])
        self.assertEqual(event['expense_kind'], 'emergency_veterinary')
        self.assertEqual(event['impact']['comfortable_capacity_change_vs_excluding_event'], -409.27)
        self.assertEqual(event['impact']['additional_future_debit'], 0)

    def test_paid_unpaid_and_anticipated_are_distinct(self):
        unpaid = self.contexts['vet_unpaid']['financial_insights']['cashflow']
        anticipated = self.contexts['vet_anticipated']['financial_insights']['cashflow']
        self.assertEqual(unpaid['runway']['balance'], 2000)
        self.assertEqual(unpaid['runway']['projected_after_known_pending'], 150)
        self.assertEqual(anticipated['runway']['projected_after_known_pending'], 2000)
        self.assertEqual(unpaid['savings_capacity']['comfortable'], 900)

    def test_pending_linked_to_posted_is_not_charged_twice(self):
        tx = demo_transactions(True)
        ctx = build_planning_context(tx, as_of=AS_OF,
            pending_expenses=(PendingExpense('vet', 1850, 'Pets', AS_OF, AS_OF,
                                             transaction_id=tx[-1].transaction_id),))
        self.assertEqual(ctx['pending_expenses'], [])
        self.assertEqual(ctx['financial_insights']['cashflow']['runway']['projected_after_known_pending'], 150)

    def test_pending_not_known_yet_is_excluded(self):
        ctx = build_planning_context([], as_of=date(2026, 1, 1),
            pending_expenses=(PendingExpense('future', 100, 'Pets', AS_OF, AS_OF),))
        self.assertEqual(ctx['pending_expenses'], [])

    def test_historical_context_ignores_all_future_data(self):
        cutoff = date(2026, 7, 31)
        tx = demo_transactions(True)
        truncated = [t for t in tx if t.date <= cutoff]
        future_snapshot = AccountSnapshot('secret-future-account', 'savings', 99999, AS_OF)
        a = build_planning_context(tx, as_of=cutoff, accounts=(future_snapshot,))
        b = build_planning_context(truncated, as_of=cutoff)
        self.assertEqual(a, b)
        # Current incomplete month excludes the event from historical savings capacity,
        # while current balances still record its full cost.
        mid = [replace(t, date=date(2026, 9, 29)) if t.transaction_id == tx[-1].transaction_id else t
               for t in tx if t.date <= date(2026, 9, 29) or t.transaction_id == tx[-1].transaction_id]
        insight = financial_insights(mid, as_of=date(2026, 9, 29))
        self.assertEqual(insight['cashflow']['savings_capacity']['comfortable'], 900)

    def test_account_scoped_recurrence_and_source_flags(self):
        tx = demo_transactions()
        bills = bill_insights(tx, AS_OF)
        self.assertTrue(any(b['recurrence'] == 'source_flagged' for b in bills))
        inferred = bill_insights([replace(t, recurring=False) for t in tx], AS_OF)
        self.assertTrue(any(b['recurrence'] == 'inferred' for b in inferred))
        self.assertTrue(all(not b['confirmed_contract'] for b in bills))
        other = [replace(t, account_id='other', transaction_id=t.transaction_id+'-other') for t in tx]
        self.assertEqual(len(bill_insights(tx+other, AS_OF)), 2*len(bills))

    def test_recurring_amount_does_not_use_future_charge(self):
        tx = [Transaction(date(2026, m, 5), 'BILL', -v, 'Utilities', 'expense',
                          account_id='a', transaction_id=str(m))
              for m, v in [(1, 20), (2, 20), (3, 20), (4, 900), (5, 900)]]
        bills = bill_insights(tx, date(2026, 3, 31))
        self.assertEqual(bills[0]['typical_amount'], 20)
        self.assertEqual(bills[0]['next_expected'], '2026-04-04')

    def test_protected_and_undesignated_savings(self):
        balances = self.normal['financial_insights']['balances']
        savings = next(b for b in balances if b['kind'] == 'savings')
        self.assertEqual(savings['protected'], 8100)
        self.assertEqual(savings['goal_available'], 1200)
        unknown = financial_insights([], as_of=AS_OF,
            accounts=(AccountSnapshot('s', 'savings', 10000, AS_OF),))
        self.assertEqual(unknown['balances'][0]['goal_available'], 0)
        loan = financial_insights([], as_of=AS_OF,
            accounts=(AccountSnapshot('loan', 'loan', -400000, AS_OF),))
        self.assertEqual(loan['balances'][0]['designation_shortfall'], 0)
        self.assertFalse(any(o['category'] in {'Health', 'Pets'} for o in
                             self.paid['financial_insights']['opportunities']))

    def test_privacy_allowlist_and_state_version(self):
        tx = [replace(t, user_id='SECRET_NAME', account_id='SECRET_ACCOUNT',
                      description='SECRET_PASSWORD_AND_ADDRESS', category='SECRET_CATEGORY')
              for t in demo_transactions()]
        c = build_planning_context(tx, as_of=AS_OF,
             goals=(Goal('SECRET_GOAL', 'holiday', 6000, 1200, date(2027,5,31)),))
        encoded = context_json(c)
        self.assertNotIn('SECRET', encoded)
        self.assertNotIn('transaction_id', encoded)
        self.assertEqual(json.loads(encoded), c)
        self.assertEqual(len(c['state_version']), 64)
        self.assertNotEqual(self.normal['state_version'], self.paid['state_version'])
        self.assertEqual(self.normal['state_version'], demo_contexts()['normal']['state_version'])

    def test_empty_and_invalid_inputs(self):
        c = build_planning_context([], as_of=AS_OF)
        self.assertEqual(c['financial_insights']['income']['confidence'], 'limited')
        self.assertEqual(c['financial_insights']['cashflow']['savings_capacity']['comfortable'], 0)
        with self.assertRaises(ValueError):
            build_planning_context([replace(demo_transactions()[0], amount=math.inf)], as_of=AS_OF)
        with self.assertRaises(ValueError):
            AccountSnapshot('a', 'savings', 100, AS_OF, protected=101)
        with self.assertRaises(ValueError):
            Preferences(protected_categories=('SECRET',))
        with self.assertRaises(ValueError):
            normalized_shocks([demo_transactions()[0]]*2, as_of=AS_OF)

    def test_future_event_evidence_is_not_used(self):
        tx = demo_transactions(True)
        context = build_planning_context(tx, as_of=AS_OF, event_evidence=(
            PostedEventEvidence(tx[-1].transaction_id, date(2026, 10, 1), "emergency_veterinary"),))
        event = next(s for s in context['shocks'] if s['event_amount'] == 1850)
        self.assertFalse(event['unexpected_confirmed'])
        self.assertEqual(event['expense_kind'], 'unknown')

    def test_duplicate_goals_pending_and_mixed_customers_rejected(self):
        goal = Goal('g', 'holiday', 6000, 1200, date(2027,5,31))
        pending = PendingExpense('p', 10, 'Pets', AS_OF, AS_OF)
        for kwargs in ({'goals': (goal,goal)}, {'pending_expenses': (pending,pending)}):
            with self.assertRaises(ValueError):
                build_planning_context([], as_of=AS_OF, **kwargs)
        tx = demo_transactions()
        with self.assertRaises(ValueError):
            financial_insights(tx+[replace(tx[0],user_id='other')], as_of=AS_OF)

    def test_example_files_are_current_and_strict_json(self):
        from pathlib import Path
        for name, context in self.contexts.items():
            stored = json.loads((Path(__file__).resolve().parent.parent/'examples'/f'{name}.json').read_text())
            self.assertEqual(stored, context)
            json.dumps(context, allow_nan=False)


class ShockDeduplicationTests(unittest.TestCase):
    def test_overlapping_alerts_sum_unique_transactions(self):
        rows = [Transaction(date(2026, 1, i), 'SMALL', -10, 'Shopping', 'expense',
                            transaction_id=f's{i}') for i in range(1,20)]
        rows.append(Transaction(date(2026, 4, 1), 'NEW LARGE', -1000, 'Shopping', 'expense', transaction_id='big'))
        shocks = normalized_shocks(rows, as_of=date(2026,4,1))
        big = [s for s in shocks if 'big' in s['transaction_ids']]
        self.assertEqual(len(big), 1)
        self.assertEqual(big[0]['event_amount'], 1000)
        self.assertEqual(set(big[0]['detectors']), {'large_single_spend', 'new_merchant_big_amount'})
        self.assertTrue(any(e['score_is_unbounded'] for e in big[0]['evidence']))
        json.dumps(shocks, allow_nan=False)

    def test_later_recurrence_cannot_erase_earlier_anomaly(self):
        start = [Transaction(date(2026,1,1), 'SMALL', -10, 'Shopping','expense', transaction_id='start')]
        later = [Transaction(date(2026,m,1), 'NEW BILL', -900, 'Shopping','expense', transaction_id=f'b{m}')
                 for m in (4,5,6)]
        shocks = normalized_shocks(start+later, as_of=date(2026,6,30))
        self.assertTrue(any('b4' in s['transaction_ids'] for s in shocks))


class CalculationToolsTests(unittest.TestCase):
    def test_read_only_contribution_arithmetic(self):
        self.assertEqual(required_contribution(target=6000, allocated=1200, months=8)['monthly_amount'], 600)
        self.assertTrue(contribution_projection(target=6000, allocated=1200,
                                                monthly_contribution=600, months=8)['target_reached'])
        self.assertIsNone(required_contribution(target=6000, allocated=1200, months=0)['monthly_amount'])
        self.assertEqual(required_contribution(target=1, allocated=0, months=3)['monthly_amount'], .34)
        cash = contribution_cashflow(monthly_contribution=600, monthly_surplus=900,
                                     liquid_balance=2000, pending_expenses=1850)
        self.assertEqual(cash['liquid_after_pending_reserve_and_contribution'], -450)
        for value in (-1, math.inf, math.nan, True):
            with self.assertRaises(ValueError):
                required_contribution(target=value, allocated=0, months=8)


if __name__ == '__main__':
    unittest.main()
