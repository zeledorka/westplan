"""Run from backend/:  python -m unittest discover tests -v"""
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finstats import (  # noqa: E402
    MonthlyFlow, ShockType, Transaction, bnpl_plans, check_runway, detect_all_shocks,
    detect_bnpl_stacking, detect_bursts, detect_overdrafts, detect_savings_drawdowns,
    detect_category_spikes, detect_large_single_spends, detect_new_merchant_big_amount,
    detect_recurring_bill_changes, detect_recurring_series, detect_runway_shocks, ewma,
    load_statement_csv, mad, median, modified_z, monthly_flows, normalize_merchant, rolling_mean,
    savings_capacity_from_flows,
)

_ids = iter(range(1, 10**6))


def T(d, amount, category="Shopping", desc="SHOP", kind=None, essential=False, recurring=False,
      bal=None, acct="a"):
    return Transaction(date=d, description=desc, amount=amount, category=category,
                       type=kind or ("income" if amount > 0 else "expense"), essential=essential,
                       recurring=recurring, balance_after=bal, account_id=acct,
                       transaction_id=f"t{next(_ids)}")


D0 = date(2026, 1, 5)  # a Monday


class StatsTests(unittest.TestCase):
    def test_median_mad(self):
        self.assertEqual(median([3, 1, 2]), 2)
        self.assertEqual(median([1, 2, 3, 4]), 2.5)
        self.assertEqual(mad([1, 2, 3, 4, 100]), 1)

    def test_modified_z(self):
        self.assertAlmostEqual(modified_z(100, [1, 2, 3, 4, 100]), 0.6745 * 97)
        self.assertEqual(modified_z(5, [5, 5, 5]), 0)
        self.assertTrue(math.isinf(modified_z(6, [5, 5, 5])))

    def test_ewma_and_rolling(self):
        self.assertEqual(ewma([1, 1, 1], alpha=0.5), 1)
        self.assertEqual(ewma([0, 10], alpha=0.5), 5)
        self.assertEqual(rolling_mean([1, 2, 3, 4], 3), [2, 3])

    def test_normalize_merchant(self):
        self.assertEqual(normalize_merchant("SQ *Rosie's Cafe #0412"), "ROSIES CAFE")
        self.assertEqual(normalize_merchant("WOOLWORTHS NEWTOWN", ["Newtown"]), "WOOLWORTHS")


class SavingsTests(unittest.TestCase):
    def flows(self, inc, ess, disc, n=6):
        return [MonthlyFlow(f"2026-{m:02d}", inc, ess, disc) for m in range(1, n + 1)]

    def test_steady_surplus(self):
        cap = savings_capacity_from_flows(self.flows(5000, 3000, 1000))
        self.assertEqual(cap.centre, 1000)
        self.assertEqual(cap.sigma_w, 0)
        self.assertEqual(cap.comfortable, 1000)
        self.assertEqual(cap.stretch, 1200)  # + 0.2 * 1000

    def test_income_cap(self):
        cap = savings_capacity_from_flows(self.flows(5000, 3000, 1000), gamma=0.1)
        self.assertEqual((cap.comfortable, cap.stretch), (500, 500))

    def test_deficit_floors_at_zero(self):
        cap = savings_capacity_from_flows(self.flows(3000, 3000, 500))
        self.assertEqual((cap.comfortable, cap.stretch), (0, 0))

    def test_monthly_flows(self):
        txns = [
            T(date(2026, 1, 1), 1000, "Income"),
            T(date(2026, 1, 5), -300, "Housing", essential=True),
            T(date(2026, 1, 6), -100),
            T(date(2026, 1, 7), -500, "Transfers", kind="transfer"),
            T(date(2026, 2, 1), 1000, "Income"),
            T(date(2026, 3, 31), -50),
        ]
        got = [(f.month, f.income, f.essential, f.discretionary) for f in monthly_flows(txns)]
        self.assertEqual(got, [("2026-01", 1000, 300, 100), ("2026-02", 1000, 0, 0), ("2026-03", 0, 0, 50)])
        # a partial trailing month is dropped
        got = monthly_flows(txns + [T(date(2026, 4, 10), -20)])
        self.assertEqual([f.month for f in got], ["2026-01", "2026-02", "2026-03"])


class ShockTests(unittest.TestCase):
    def test_large_single_spend(self):
        amounts = [40, 45, 50, 55, 60] * 2
        txns = [T(D0 + timedelta(i), -a) for i, a in enumerate(amounts)]
        txns += [T(D0 + timedelta(11), -1000), T(D0 + timedelta(12), -150)]
        shocks = detect_large_single_spends(txns)
        self.assertEqual([s.amount for s in shocks], [1000])

    def test_burst(self):
        txns = [T(D0 + timedelta(i), -10) for i in range(30)]
        txns += [T(D0 + timedelta(30), -10) for _ in range(8)]
        shocks = detect_bursts(txns)
        self.assertEqual(len(shocks), 1)
        self.assertEqual(shocks[0].date, D0 + timedelta(30))
        self.assertEqual(shocks[0].details["count"], 8)

    def test_category_spike(self):
        txns = [T(D0 + timedelta(weeks=w), -100, "Groceries", f"SHOP {chr(65 + w)}") for w in range(9)]
        txns += [T(D0 + timedelta(weeks=9, days=d), -150, "Groceries", f"BULK {chr(65 + d)}") for d in range(4)]
        shocks = detect_category_spikes(txns)
        self.assertEqual([(s.date, s.amount) for s in shocks], [(date(2026, 3, 9), 600)])
        self.assertEqual(len(shocks[0].transaction_ids), 4)

    def test_single_purchase_week_is_left_to_other_detectors(self):
        txns = [T(D0 + timedelta(weeks=w), -100, "Groceries", f"SHOP {chr(65 + w)}") for w in range(9)]
        txns.append(T(D0 + timedelta(weeks=9), -600, "Groceries", "ONE BIG SHOP"))
        self.assertEqual(detect_category_spikes(txns), [])
        self.assertEqual(len(detect_category_spikes(txns, max_single_share=None)), 1)

    def test_monthly_bill_without_flag_is_not_a_spike(self):
        # CSV statements carry no recurring flag; a monthly bill must not read as a weekly spike
        txns = [T(D0 + timedelta(days=30 * m), -142, "Insurance", "HCF HEALTH") for m in range(8)]
        txns += [T(D0 + timedelta(weeks=w), -60, "Groceries", f"SHOP {chr(65 + w)}") for w in range(35)]
        self.assertEqual(detect_category_spikes(txns), [])

    def test_recurring_series_and_bill_change(self):
        days = [date(2026, 1, 5), date(2026, 2, 4), date(2026, 3, 6), date(2026, 4, 5), date(2026, 5, 5)]
        txns = [T(d, -a, "Subscriptions", "NETFLIX.COM 123") for d, a in zip(days, [20, 20, 20, 20, 25])]
        cafe = [0, 1, 6, 8, 17]
        txns += [T(D0 + timedelta(i), -12, "Eating out", "CAFE") for i in cafe]
        series = detect_recurring_series(txns)
        self.assertEqual([s.merchant for s in series], ["NETFLIX COM"])
        self.assertEqual(series[0].period_days, 30)
        self.assertEqual(series[0].upcoming(date(2026, 5, 5), 40), [date(2026, 6, 4)])
        shocks = detect_recurring_bill_changes(txns)
        self.assertEqual(len(shocks), 1)
        self.assertAlmostEqual(shocks[0].score, 0.25)

    def test_step_change_flagged_once(self):
        days = [date(2026, 1, 3) + timedelta(days=30 * m) for m in range(8)]
        txns = [T(d, -(7200 if m < 5 else 7992), "Loan repayments", "HOME LOAN") for m, d in enumerate(days)]
        shocks = detect_recurring_bill_changes(txns)
        self.assertEqual([s.date for s in shocks], [days[5]])

    def test_variable_bill_needs_history(self):
        # +/-20% wobble on a shared bill: a 30% move after four charges is not enough evidence
        days = [date(2026, 1, 12) + timedelta(days=30 * m) for m in range(5)]
        txns = [T(d, -a, "Utilities", "SPLIT BILLS") for d, a in zip(days, [65, 58, 74, 62, 84])]
        self.assertEqual(detect_recurring_bill_changes(txns), [])

    def test_recurring_merchant_is_not_new(self):
        start = date(2026, 1, 1)
        txns = [T(start, -50, desc="SHOP A")]
        txns += [T(start + timedelta(days=30 * m), -480, "Transport", "MECHANIC", recurring=True) for m in range(3)]
        txns.append(T(start + timedelta(days=75), -3000, "Transport", "MECHANIC"))
        self.assertEqual(detect_new_merchant_big_amount(txns), [])

    def test_new_merchant_big_amount(self):
        start = date(2026, 1, 1)
        txns = [
            T(start, -50, desc="SHOP A"),
            T(start + timedelta(1), -900, desc="BIG"),          # inside warm-up
            T(start + timedelta(70), -500, desc="NEW STORE"),   # flagged
            T(start + timedelta(71), -500, desc="NEW STORE"),   # seen already
            T(start + timedelta(72), -100, desc="OTHER"),       # under floor
        ]
        shocks = detect_new_merchant_big_amount(txns)
        self.assertEqual([(s.date, s.amount) for s in shocks], [(start + timedelta(70), 500)])

    def rent_txns(self):
        rent = [(date(2026, 1, 1), 5000), (date(2026, 1, 31), 4000), (date(2026, 3, 2), 3000),
                (date(2026, 4, 1), 1100)]
        txns = [T(d, -1000, "Housing", "RENT", essential=True, recurring=True, bal=b) for d, b in rent]
        txns.append(T(date(2026, 4, 25), -5, "Eating out", "COFFEE", bal=1095))
        return txns

    def test_check_runway(self):
        series = detect_recurring_series(self.rent_txns())
        shock = check_runway(1100, date(2026, 4, 20), series, horizon_days=14, buffer=200)
        self.assertIsNotNone(shock)
        self.assertEqual(shock.amount, 100)
        self.assertIsNone(check_runway(2000, date(2026, 4, 20), series, horizon_days=14, buffer=200))

    def test_runway_counts_only_bills_before_payday(self):
        txns = self.rent_txns()
        pay = [T(date(2026, 1, 1) + timedelta(days=14 * k), 900, "Income", "PAYROLL") for k in range(10)]
        series = detect_recurring_series(txns)
        income = detect_recurring_series(pay, kind="income")
        # pay lands 2026-04-23, before rent on 2026-05-01, so the rent is not counted
        self.assertIsNone(check_runway(1100, date(2026, 4, 20), series, 14, 200, income_series=income))
        self.assertIsNotNone(check_runway(1100, date(2026, 4, 20), series, 14, 200))

    def test_runway_scan(self):
        shocks = detect_runway_shocks(self.rent_txns())
        self.assertEqual([s.date for s in shocks], [date(2026, 4, 25)])

    def test_detect_all_sorted(self):
        shocks = detect_all_shocks(self.rent_txns())
        self.assertEqual([s.date for s in shocks], sorted(s.date for s in shocks))
        self.assertIn(ShockType.RUNWAY, {s.type for s in shocks})


class CashflowStressTests(unittest.TestCase):
    def test_overdraft_one_alert_per_episode(self):
        rows = [(1, -100, 300), (2, -400, -100), (2, 200, 100), (3, -150, -50), (3, 100, 50),
                (10, 2000, 2050), (11, -2100, -50)]
        txns = [T(D0 + timedelta(d), a, desc=f"ROW {chr(65 + i)}", kind="transfer" if a > 0 else None, bal=b)
                for i, (d, a, b) in enumerate(rows)]
        shocks = detect_overdrafts(txns)
        # day 3 dip is the same episode (balance never recovered by $500); day 11 is a new one
        self.assertEqual([s.date for s in shocks], [D0 + timedelta(2), D0 + timedelta(11)])
        self.assertTrue(shocks[0].details["covered_same_day"])
        self.assertFalse(shocks[1].details["covered_same_day"])

    def test_savings_drawdown_episodes(self):
        spend = [T(D0 + timedelta(d), -20, desc="SHOP") for d in range(40)]
        top = [(1, 100), (2, 100), (30, 150), (31, 200)]   # 2 x $100 is below floor and count
        moves = [T(D0 + timedelta(d), a, "Transfers", "TRANSFER FROM ESAVER", kind="transfer") for d, a in top]
        moves.append(T(D0 + timedelta(5), 999, "Transfers", "TRANSFER FROM CHOICE", kind="transfer", acct="sav"))
        shocks = detect_savings_drawdowns(spend + moves)
        self.assertEqual([(s.date, s.amount) for s in shocks], [(D0 + timedelta(31), 350)])

    def test_bnpl_plans_and_stacking(self):
        def ap(shop, k, d):
            return T(D0 + timedelta(d), -25, "Buy Now Pay Later", f"AFTERPAY - {shop} ({k}/4)")
        txns = [ap("ONE", k, 14 * (k - 1)) for k in range(1, 5)]          # days 0-42
        txns += [ap("TWO", k, 10 + 14 * (k - 1)) for k in range(1, 5)]    # days 10-52
        txns += [ap("THREE", k, 42 + 14 * (k - 1)) for k in range(1, 3)]  # starts day 42, ONE ends day 42
        txns += [ap("FOUR", k, 50 + 14 * (k - 1)) for k in range(1, 3)]   # TWO, THREE, FOUR active
        txns.append(T(D0, 5000, "Income", "PAY"))
        plans = bnpl_plans(txns)
        self.assertEqual([(p.key, p.instalments) for p in plans][:2], [("AFTERPAY ONE", 4), ("AFTERPAY TWO", 4)])
        self.assertEqual(plans[0].end, D0 + timedelta(42))
        stacked = [s for s in detect_bnpl_stacking(txns, max_income_share=None)]
        self.assertEqual([s.date for s in stacked], [D0 + timedelta(50)])

    def test_bnpl_income_share(self):
        txns = [T(D0, 1000, "Income", "PAY"), T(D0 + timedelta(1), -60, "Buy Now Pay Later", "ZIP PAY AUTO REPAYMENT"),
                T(D0 + timedelta(15), -60, "Buy Now Pay Later", "ZIP PAY AUTO REPAYMENT")]
        shocks = detect_bnpl_stacking(txns)
        self.assertEqual([(s.details["rule"], s.amount) for s in shocks], [("income_share", 120)])


STATEMENTS = Path(__file__).resolve().parent.parent / "data" / "statements"
EXAMPLE_FILES = ["Mia_Tran", "Priya_Sharma", "David_Kowalski", "Margaret_Obrien", "Jake_Morrison"]


@unittest.skipUnless(all((STATEMENTS / f"{n}.csv").exists() for n in EXAMPLE_FILES), "example statements missing")
class ExampleStatementTests(unittest.TestCase):
    """The example statements carry planted shock events (transaction IDs like u003-S0001).
    Every planted event must be flagged by at least one detector, and every customer must
    have at least one shock."""

    @classmethod
    def setUpClass(cls):
        cls.txns = {n: load_statement_csv(STATEMENTS / f"{n}.csv") for n in EXAMPLE_FILES}
        cls.shocks = {n: detect_all_shocks(t) for n, t in cls.txns.items()}

    def test_full_layout_parsed(self):
        mia = self.txns["Mia_Tran"]
        self.assertEqual({t.account_id for t in mia}, {"u001-everyday", "u001-savings"})
        rent = next(t for t in mia if t.description.startswith("RENT"))
        self.assertEqual((rent.amount, rent.type, rent.essential, rent.recurring), (-230.0, "expense", True, True))
        pay = next(t for t in mia if t.description == "BEAN SCENE CAFE WAGES")
        self.assertEqual((pay.type, pay.amount > 0), ("income", True))

    def test_every_customer_has_a_shock(self):
        for name, shocks in self.shocks.items():
            with self.subTest(name=name):
                self.assertGreater(len(shocks), 0)

    def test_every_planted_event_is_flagged(self):
        for name, txns in self.txns.items():
            flagged = {i for s in self.shocks[name] for i in s.transaction_ids}
            planted = [t for t in txns if "-S" in t.transaction_id]
            self.assertTrue(planted, f"{name}: no planted events in the statement")
            for t in planted:
                with self.subTest(name=name, event=t.description):
                    self.assertIn(t.transaction_id, flagged)

    def test_separate_bnpl_plans_are_not_a_price_change(self):
        # two Afterpay plans at THE ICONIC (Dec-Jan at $34.99, Jun-Jul at $46.75) are separate runs
        changes = [s for s in self.shocks["Mia_Tran"] if s.type == ShockType.RECURRING_BILL_CHANGE
                   and "ICONIC" in s.details["merchant"]]
        self.assertEqual(changes, [])

    def of_type(self, name, kind):
        return [s for s in self.shocks[name] if s.type == kind]

    def test_overdrafts_match_negative_balances(self):
        expect = {"Priya_Sharma": [date(2026, 7, 10), date(2026, 10, 3)],
                  "David_Kowalski": [date(2026, 8, 12), date(2026, 11, 5)],
                  "Jake_Morrison": [date(2026, 5, 2)],       # four dips in one week, one episode
                  "Mia_Tran": [], "Margaret_Obrien": []}
        for name, days in expect.items():
            with self.subTest(name=name):
                self.assertEqual([s.date for s in self.of_type(name, ShockType.OVERDRAFT)], days)

    def test_savings_drawdowns(self):
        david = self.of_type("David_Kowalski", ShockType.SAVINGS_DRAWDOWN)
        self.assertEqual([(s.date, s.amount) for s in david],
                         [(date(2026, 8, 12), 2100), (date(2026, 11, 5), 400)])
        self.assertEqual(self.of_type("Mia_Tran", ShockType.SAVINGS_DRAWDOWN), [])

    def test_bnpl_income_share_for_student(self):
        mia = self.of_type("Mia_Tran", ShockType.BNPL_STACKING)
        self.assertEqual([(s.date.strftime("%Y-%m"), s.details["rule"]) for s in mia], [("2026-08", "income_share")])

    def test_noise_stays_reasonable(self):
        # about two alerts a month at most, not dozens
        for name, shocks in self.shocks.items():
            with self.subTest(name=name):
                self.assertLessEqual(len(shocks), 24)


if __name__ == "__main__":
    unittest.main()
