"""Run from backend/:  python -m unittest discover tests -v"""
import math
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from finstats import (  # noqa: E402
    MonthlyFlow, ShockType, Transaction, check_runway, detect_all_shocks, detect_bursts,
    detect_category_spikes, detect_large_single_spends, detect_new_merchant_big_amount,
    detect_recurring_bill_changes, detect_recurring_series, detect_runway_shocks, ewma,
    load_transactions, mad, median, modified_z, monthly_flows, normalize_merchant, rolling_mean,
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


MOCK = Path(__file__).resolve().parent.parent / "data" / "mock_transactions.json"


@unittest.skipUnless(MOCK.exists(), "mock data not generated")
class InjectedShockTests(unittest.TestCase):
    """generate_mock_data.py plants one shock per customer; each must be detected on its day,
    by the right detector, pointing at the planted transactions."""

    EXPECTED = {
        "u001": (ShockType.BURST, date(2026, 8, 22), "PAYPAL *GAMEKEYS"),
        "u002": (ShockType.RECURRING_BILL_CHANGE, date(2026, 7, 3), "HOME LOAN REPAYMENT"),
        "u003": (ShockType.LARGE_SINGLE_SPEND, date(2026, 9, 3), "ULTRA TUNE CAR SERVICE"),
        "u004": (ShockType.NEW_MERCHANT_BIG_AMOUNT, date(2026, 6, 10), "CENTRAL COAST DENTAL IMPLANTS"),
        "u005": (ShockType.CATEGORY_SPIKE, date(2026, 8, 10), "SYDNEY TOOLS"),
    }

    @classmethod
    def setUpClass(cls):
        cls.txns = {uid: load_transactions(MOCK, uid) for uid in cls.EXPECTED}
        cls.shocks = {uid: detect_all_shocks(t) for uid, t in cls.txns.items()}

    def test_each_injected_shock_is_detected(self):
        for uid, (kind, day, desc) in self.EXPECTED.items():
            with self.subTest(uid=uid, kind=kind):
                by_id = {t.transaction_id: t for t in self.txns[uid]}
                hits = [s for s in self.shocks[uid] if s.type == kind and s.date == day]
                self.assertEqual(len(hits), 1, f"{uid}: no {kind} on {day}")
                self.assertIn(desc, {by_id[i].description for i in hits[0].transaction_ids})

    def test_home_loan_rise_flagged_once(self):
        loan = [s for s in self.shocks["u002"] if s.type == ShockType.RECURRING_BILL_CHANGE
                and s.details["merchant"] == "HOME LOAN REPAYMENT"]
        self.assertEqual([s.date for s in loan], [date(2026, 7, 3)])

    def test_noise_stays_low(self):
        # a year of transactions should produce a handful of alerts per person, not dozens
        for uid, shocks in self.shocks.items():
            with self.subTest(uid=uid):
                self.assertLessEqual(len(shocks), 15)


if __name__ == "__main__":
    unittest.main()
