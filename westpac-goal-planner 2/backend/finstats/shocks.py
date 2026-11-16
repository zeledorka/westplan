"""Spending shock detectors.

| Type                     | Rule                                                                         |
|--------------------------|------------------------------------------------------------------------------|
| Large single spend       | Modified z > 3.5 within the category, and above a dollar floor               |
| Burst / velocity         | Transaction count in a day above baseline daily median + k * MAD             |
| Category spike           | Weekly category total vs the trailing 8-week median / MAD                    |
| Recurring bill change    | Stable-interval merchant series whose amount moves more than e.g. 10%        |
| New merchant, big amount | First-ever merchant and amount above a floor                                 |
| Runway shock             | Balance minus upcoming expected recurring bills drops below a buffer         |

Every detector takes a list of Transaction (any order), only looks at past data when judging a
transaction (no look-ahead, except the runway scan's recurring-series detection), and returns
a list of Shock sorted by date. All thresholds are keyword arguments.

The mock data has dates but no times, so the burst detector's "24h window" is a calendar day.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable

from .data import Transaction, normalize_merchant, primary_account, sort_transactions
from .stats import mad, mean, median, modified_z, quantile, std


class ShockType:
    LARGE_SINGLE_SPEND = "large_single_spend"
    BURST = "burst"
    CATEGORY_SPIKE = "category_spike"
    RECURRING_BILL_CHANGE = "recurring_bill_change"
    NEW_MERCHANT_BIG_AMOUNT = "new_merchant_big_amount"
    RUNWAY = "runway"


@dataclass(frozen=True)
class Shock:
    type: str
    date: date
    amount: float                 # dollars involved (spend, weekly total, projected balance...)
    score: float                  # detector-specific strength (z-score, % change, shortfall...)
    message: str
    transaction_ids: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)


def _sorted_by_date(shocks: list[Shock]) -> list[Shock]:
    return sorted(shocks, key=lambda s: s.date)


def _expenses(txns: Iterable[Transaction]) -> list[Transaction]:
    return sort_transactions(t for t in txns if t.is_expense and t.amount < 0)


def _without_recurring(txns: list[Transaction]) -> list[Transaction]:
    """Drop transactions flagged recurring or belonging to a detected recurring series.

    Detection covers sources without a recurring flag (CSV statements) and uses the whole
    history, so the first charge of a bill that later repeats is treated as a known bill.
    """
    known = {id(t) for s in detect_recurring_series(txns) for t in s.occurrences}
    return [t for t in txns if not t.recurring and id(t) not in known]


# --------------------------------------------------------------------------------------------
# 1. Large single spend
# --------------------------------------------------------------------------------------------

def score_single_spend(spend: float, category_history: list[float]) -> float:
    """Modified z-score of one spend (positive dollars) against past spends in its category."""
    return modified_z(spend, category_history)


def detect_large_single_spends(txns: Iterable[Transaction], z_threshold: float = 3.5,
                               floor: float = 200.0, min_history: int = 8,
                               lookback_days: int = 365,
                               exclude_recurring: bool = True) -> list[Shock]:
    """Flag a spend whose modified z within its category exceeds z_threshold and spend >= floor.

    The baseline is that category's earlier spends within lookback_days (at least min_history).
    Recurring bills (flagged or detected) are skipped by default: rent and loans are big but expected.
    """
    exp = _expenses(txns)
    if exclude_recurring:
        exp = _without_recurring(exp)
    history: dict[str, deque] = defaultdict(deque)  # category -> deque[(date, spend)]
    out = []
    for t in exp:
        h = history[t.category]
        cutoff = t.date - timedelta(days=lookback_days)
        while h and h[0][0] < cutoff:
            h.popleft()
        past = [s for _, s in h]
        if len(past) >= min_history and t.spend >= floor:
            z = score_single_spend(t.spend, past)
            if z > z_threshold:
                med = median(past)
                out.append(Shock(
                    ShockType.LARGE_SINGLE_SPEND, t.date, round(t.spend, 2), round(z, 2),
                    f"${t.spend:,.2f} at {t.description} is unusually large for {t.category} "
                    f"(typical ${med:,.2f})",
                    (t.transaction_id,),
                    dict(category=t.category, category_median=round(med, 2), history_size=len(past)),
                ))
        h.append((t.date, t.spend))
    return out


# --------------------------------------------------------------------------------------------
# 2. Burst / velocity
# --------------------------------------------------------------------------------------------

def detect_bursts(txns: Iterable[Transaction], k: float = 3.0, baseline_days: int = 56,
                  min_baseline_days: int = 14, min_excess: float = 3.0) -> list[Shock]:
    """Flag a day whose expense count exceeds median + max(k * MAD, min_excess) of the trailing
    baseline_days daily counts (zero-spend days count as 0).

    min_excess stops a MAD of 0 (very regular spenders) from flagging a single extra purchase.
    """
    exp = _expenses(txns)
    if not exp:
        return []
    by_day: dict[date, list[Transaction]] = defaultdict(list)
    for t in exp:
        by_day[t.date].append(t)
    first, last = exp[0].date, exp[-1].date
    days = [first + timedelta(n) for n in range((last - first).days + 1)]
    counts = [len(by_day.get(d, ())) for d in days]

    out = []
    for i, d in enumerate(days):
        if i < min_baseline_days:
            continue
        base = counts[max(0, i - baseline_days):i]
        med, m = median(base), mad(base)
        threshold = med + max(k * m, min_excess)
        if counts[i] > threshold:
            day_txns = by_day[d]
            total = sum(t.spend for t in day_txns)
            score = (counts[i] - med) / m if m > 0 else counts[i] - med
            out.append(Shock(
                ShockType.BURST, d, round(total, 2), round(score, 2),
                f"{counts[i]} purchases on {d.isoformat()} (usually about {med:g} a day), "
                f"${total:,.2f} in total",
                tuple(t.transaction_id for t in day_txns),
                dict(count=counts[i], baseline_median=med, baseline_mad=m, threshold=threshold),
            ))
    return out


# --------------------------------------------------------------------------------------------
# 3. Category spike
# --------------------------------------------------------------------------------------------

def week_start(d: date) -> date:
    """Monday of d's week."""
    return d - timedelta(days=d.weekday())


def _weekly_buckets(txns: Iterable[Transaction], exclude_recurring: bool
                    ) -> tuple[list[date], dict[str, list[list[Transaction]]]]:
    exp = _expenses(txns)
    if exclude_recurring:
        exp = _without_recurring(exp)
    if not exp:
        return [], {}
    first = week_start(exp[0].date)
    if exp[0].date != first:
        first += timedelta(days=7)
    n_weeks = (week_start(exp[-1].date) - first).days // 7 + 1
    weeks = [first + timedelta(weeks=i) for i in range(max(0, n_weeks))]
    buckets: dict[str, list[list[Transaction]]] = defaultdict(lambda: [[] for _ in weeks])
    for t in exp:
        i = (week_start(t.date) - first).days // 7
        if 0 <= i < len(weeks):
            buckets[t.category][i].append(t)
    return weeks, dict(buckets)


def weekly_category_totals(txns: Iterable[Transaction],
                           exclude_recurring: bool = True) -> tuple[list[date], dict[str, list[float]]]:
    """Return (week_starts, {category: [total spend per week]}), including zero weeks.

    Recurring bills (flagged or detected) are left out by default: a monthly bill lands in one
    week in four, so it would read as a spike against a $0 weekly median every time.
    A partial first week (data not starting on a Monday) is dropped so it cannot drag the
    baseline down.
    """
    weeks, buckets = _weekly_buckets(txns, exclude_recurring)
    return weeks, {c: [sum(t.spend for t in wk) for wk in b] for c, b in buckets.items()}


def detect_category_spikes(txns: Iterable[Transaction], trailing_weeks: int = 8,
                           z_threshold: float = 3.5, floor: float = 100.0,
                           exclude_recurring: bool = True, history_weeks: int = 52,
                           history_quantile: float | None = 0.95,
                           history_factor: float = 1.5,
                           max_single_share: float | None = 0.6) -> list[Shock]:
    """Flag a category-week whose total has modified z > z_threshold against the previous
    trailing_weeks weekly totals, and is at least `floor` dollars above their median.

    Extra guards (each can be turned off with None):
    - history_quantile: sparse categories (a purchase every month or two) have a trailing
      median of $0, so any purchase scores as a spike. The week must also exceed
      history_factor times this quantile of up to history_weeks earlier weeks, i.e. be big for
      this person, not just bigger than an empty month.
    - max_single_share: if one transaction makes up more than this share of the week's excess
      over the median, the week is left to the large-single-spend and new-merchant detectors.
      Category spikes then mean "spending a lot more, across several purchases".
    """
    weeks, buckets = _weekly_buckets(txns, exclude_recurring)
    out = []
    for cat, b in buckets.items():
        series = [sum(t.spend for t in wk) for wk in b]
        for i in range(trailing_weeks, len(weeks)):
            cur = series[i]
            hist = series[i - trailing_weeks:i]
            med = median(hist)
            if cur - med < floor:
                continue
            if history_quantile is not None:
                long_run = series[max(0, i - history_weeks):i]
                if cur <= history_factor * quantile(long_run, history_quantile):
                    continue
            if max_single_share is not None and max(t.spend for t in b[i]) > max_single_share * (cur - med):
                continue
            z = modified_z(cur, hist)
            if z > z_threshold:
                out.append(Shock(
                    ShockType.CATEGORY_SPIKE, weeks[i], round(cur, 2), round(z, 2),
                    f"{cat} spend of ${cur:,.2f} over {len(b[i])} purchases in the week of "
                    f"{weeks[i].isoformat()} vs a usual ${med:,.2f}",
                    tuple(t.transaction_id for t in b[i]),
                    dict(category=cat, week_start=weeks[i].isoformat(), trailing_median=round(med, 2),
                         trailing_mad=round(mad(hist), 2), purchases=len(b[i])),
                ))
    return _sorted_by_date(out)


# --------------------------------------------------------------------------------------------
# 4. Recurring series and bill changes
# --------------------------------------------------------------------------------------------

@dataclass
class RecurringSeries:
    merchant: str
    category: str
    account_id: str
    period_days: float
    tolerance_days: float
    occurrences: list[Transaction]
    kind: str = "expense"      # "expense" (bills) or "income" (pay)

    @property
    def typical_amount(self) -> float:
        """Median of the last three amounts (positive dollars)."""
        return median([abs(t.amount) for t in self.occurrences[-3:]])

    @property
    def last_date(self) -> date:
        return self.occurrences[-1].date

    @property
    def next_expected(self) -> date:
        return self.last_date + timedelta(days=round(self.period_days))

    def upcoming(self, as_of: date, horizon_days: int) -> list[date]:
        """Expected charge dates in (as_of, as_of + horizon_days], projected from the last
        occurrence on or before as_of. Empty if the series had not started or has lapsed."""
        past = [t.date for t in self.occurrences if t.date <= as_of]
        if not past:
            return []
        last = past[-1]
        if (as_of - last).days > self.period_days + self.tolerance_days:
            return []  # lapsed: a charge was missed, assume cancelled
        end = as_of + timedelta(days=horizon_days)
        out, k = [], 1
        while True:
            d = last + timedelta(days=round(k * self.period_days))
            if d > end:
                return out
            if d > as_of:
                out.append(d)
            k += 1


def detect_recurring_series(txns: Iterable[Transaction], min_occurrences: int = 3,
                            tolerance_days: float = 3.0, rel_tolerance: float = 0.10,
                            min_stable_fraction: float = 0.8, min_period_days: float = 6.0,
                            max_amount_cv: float = 0.5, drop_tokens: Iterable[str] = (),
                            kind: str = "expense") -> list[RecurringSeries]:
    """Find merchants charged (or payers paying, with kind="income") on a stable interval.

    Group by normalised merchant; the period is the median gap between charges. A group
    is recurring when at least min_stable_fraction of gaps sit within
    max(tolerance_days, rel_tolerance * period) of it (so ~30 days +/-3, ~365 days +/-36).
    max_amount_cv (std/mean of amounts) filters out everyday shopping that happens to be regular.
    """
    if kind == "expense":
        source = _expenses(txns)
    elif kind == "income":
        source = sort_transactions(t for t in txns if t.is_income and t.amount > 0)
    else:
        raise ValueError("kind must be 'expense' or 'income'")
    groups: dict[str, list[Transaction]] = defaultdict(list)
    for t in source:
        groups[normalize_merchant(t.description, drop_tokens)].append(t)

    out = []
    for merchant, occ in groups.items():
        if len(occ) < min_occurrences:
            continue
        gaps = [(b.date - a.date).days for a, b in zip(occ, occ[1:])]
        period = median(gaps)
        if period < min_period_days:
            continue
        tol = max(tolerance_days, rel_tolerance * period)
        if sum(abs(g - period) <= tol for g in gaps) / len(gaps) < min_stable_fraction:
            continue
        amounts = [abs(t.amount) for t in occ]
        if mean(amounts) > 0 and std(amounts) / mean(amounts) > max_amount_cv:
            continue
        out.append(RecurringSeries(merchant, occ[-1].category, occ[-1].account_id, period, tol, occ, kind))
    return out


def detect_recurring_bill_changes(txns: Iterable[Transaction], pct_threshold: float = 0.10,
                                  baseline_n: int = 3, min_baseline: int = 2,
                                  z_threshold: float | None = 3.5, history_n: int = 12,
                                  min_variable_history: int = 6,
                                  series: list[RecurringSeries] | None = None,
                                  **series_kwargs) -> list[Shock]:
    """Flag a recurring charge that differs from the median of its previous baseline_n charges
    by more than pct_threshold (0.10 = 10%), up or down.

    Two extra guards cut noise:
    - the charge must also differ from the charge just before it by more than pct_threshold,
      so a step change (a rate rise) is flagged once, not again while the median catches up;
    - its modified z against up to history_n earlier charges must exceed z_threshold, so bills
      that always vary (shared power bills, quarterly energy) only flag on an unusual move.
      A fixed-price bill has a MAD of 0, so any change of more than pct_threshold passes this.
      Set z_threshold=None to turn this guard off.
    - a bill whose past amounts vary (more than 2% between lowest and highest) needs at least
      min_variable_history earlier charges before it can be flagged; with only a few points,
      ordinary variation looks like a change. Fixed-price bills can be flagged straight away.

    Pass precomputed `series`, or any detect_recurring_series keyword to tune detection.
    """
    if series is None:
        series = detect_recurring_series(txns, **series_kwargs)
    out = []
    for s in series:
        amounts = [p.spend for p in s.occurrences]
        for j, t in enumerate(s.occurrences):
            prev = amounts[max(0, j - baseline_n):j]
            if len(prev) < min_baseline:
                continue
            base, last = median(prev), amounts[j - 1]
            if base <= 0 or last <= 0:
                continue
            change = (t.spend - base) / base
            if abs(change) <= pct_threshold or abs(t.spend - last) / last <= pct_threshold:
                continue
            history = amounts[max(0, j - history_n):j]
            if (max(history) - min(history)) / median(history) > 0.02 and len(history) < min_variable_history:
                continue
            if z_threshold is not None and abs(modified_z(t.spend, history)) <= z_threshold:
                continue
            direction = "up" if change > 0 else "down"
            out.append(Shock(
                ShockType.RECURRING_BILL_CHANGE, t.date, round(t.spend, 2), round(change, 4),
                f"{s.merchant} went {direction} {abs(change):.0%} to ${t.spend:,.2f} "
                f"(was ${base:,.2f}, every ~{s.period_days:g} days)",
                (t.transaction_id,),
                dict(merchant=s.merchant, category=s.category, previous=round(base, 2),
                     period_days=s.period_days),
            ))
    return _sorted_by_date(out)


# --------------------------------------------------------------------------------------------
# 5. New merchant, big amount
# --------------------------------------------------------------------------------------------

def detect_new_merchant_big_amount(txns: Iterable[Transaction], floor: float = 300.0,
                                   warmup_days: int = 60,
                                   known_merchants: Iterable[str] = (),
                                   drop_tokens: Iterable[str] = (),
                                   exclude_recurring: bool = True) -> list[Shock]:
    """Flag the first-ever spend at a merchant when it is at least `floor` dollars.

    Nothing is flagged during the first warmup_days of history (everything looks new then).
    Pass known_merchants (normalised keys) to seed what has been seen before.
    Recurring bills (flagged or detected) are skipped by default, so the first quarterly
    rates or energy bill in a short history is not reported as a new merchant.
    """
    exp = _expenses(txns)
    if not exp:
        return []
    start = exp[0].date + timedelta(days=warmup_days)
    skip = {id(t) for t in exp} - {id(t) for t in _without_recurring(exp)} if exclude_recurring else set()
    seen = set(known_merchants)
    out = []
    for t in exp:
        key = normalize_merchant(t.description, drop_tokens)
        # recurring charges are never flagged, but still mark their merchant as seen
        if key not in seen and id(t) not in skip and t.date >= start and t.spend >= floor:
            out.append(Shock(
                ShockType.NEW_MERCHANT_BIG_AMOUNT, t.date, round(t.spend, 2), round(t.spend / floor, 2),
                f"First purchase at {t.description}: ${t.spend:,.2f}",
                (t.transaction_id,),
                dict(merchant=key, category=t.category),
            ))
        seen.add(key)
    return out


# --------------------------------------------------------------------------------------------
# 6. Runway shock
# --------------------------------------------------------------------------------------------

def upcoming_bills(series: Iterable[RecurringSeries], as_of: date, horizon_days: int = 14,
                   account_id: str | None = None) -> list[dict]:
    """Expected recurring charges in (as_of, as_of + horizon_days], soonest first."""
    bills = []
    for s in series:
        if s.kind != "expense" or (account_id is not None and s.account_id != account_id):
            continue
        for d in s.upcoming(as_of, horizon_days):
            bills.append(dict(date=d, merchant=s.merchant, category=s.category,
                              amount=round(s.typical_amount, 2)))
    return sorted(bills, key=lambda b: b["date"])


def next_payday(income_series: Iterable[RecurringSeries], as_of: date, horizon_days: int = 14,
                account_id: str | None = None) -> date | None:
    """Earliest expected recurring income after as_of within the horizon, or None."""
    days = [d for s in income_series
            if s.kind == "income" and (account_id is None or s.account_id == account_id)
            for d in s.upcoming(as_of, horizon_days)]
    return min(days) if days else None


@dataclass(frozen=True)
class RunwayProjection:
    as_of: date
    balance: float
    bills: list[dict]
    bills_due: float
    projected: float
    window_end: date
    payday: date | None


def project_runway(balance: float, as_of: date, series: Iterable[RecurringSeries],
                   horizon_days: int = 14, account_id: str | None = None,
                   income_series: Iterable[RecurringSeries] | None = None) -> RunwayProjection:
    """Balance minus the recurring bills due before the next pay day (if income_series is given
    and pay is expected within the horizon), otherwise within horizon_days.

    Pay is assumed to land before bills on the same day, so bills due on pay day are left out.
    """
    series = list(series)
    payday = next_payday(income_series, as_of, horizon_days, account_id) if income_series else None
    window = (payday - as_of).days - 1 if payday else horizon_days
    bills = upcoming_bills(series, as_of, window, account_id)
    due = round(sum(b["amount"] for b in bills), 2)
    return RunwayProjection(as_of, round(balance, 2), bills, due, round(balance - due, 2),
                            as_of + timedelta(days=window), payday)


def _runway_shock(p: RunwayProjection, buffer: float) -> Shock:
    until = f"before the next pay on {p.payday.isoformat()}" if p.payday else f"by {p.window_end.isoformat()}"
    return Shock(
        ShockType.RUNWAY, p.as_of, p.projected, round(buffer - p.projected, 2),
        f"After ${p.bills_due:,.2f} of bills due {until}, the balance would be "
        f"${p.projected:,.2f}, below the ${buffer:,.0f} buffer",
        (),
        dict(balance=p.balance, bills_due=p.bills_due, buffer=buffer,
             window_end=p.window_end.isoformat(), payday=p.payday.isoformat() if p.payday else None,
             bills=[{**b, "date": b["date"].isoformat()} for b in p.bills]),
    )


def check_runway(balance: float, as_of: date, series: Iterable[RecurringSeries],
                 horizon_days: int = 14, buffer: float = 200.0,
                 account_id: str | None = None,
                 income_series: Iterable[RecurringSeries] | None = None) -> Shock | None:
    """Return a runway Shock if balance minus upcoming recurring bills is below buffer, else None.
    Use this for a live check against today's balance. See project_runway for the window."""
    p = project_runway(balance, as_of, series, horizon_days, account_id, income_series)
    return _runway_shock(p, buffer) if p.projected < buffer else None


def end_of_day_balances(txns: Iterable[Transaction], account_id: str) -> list[tuple[date, float]]:
    """(date, closing balance) for each day the account had a transaction with balance_after."""
    closing: dict[date, float] = {}
    for t in sort_transactions(txns):
        if t.account_id == account_id and t.balance_after is not None:
            closing[t.date] = float(t.balance_after)
    return sorted(closing.items())


def detect_runway_shocks(txns: Iterable[Transaction], account_id: str | None = None,
                         horizon_days: int = 14, buffer: float = 200.0,
                         series: list[RecurringSeries] | None = None,
                         income_series: list[RecurringSeries] | None = None,
                         until_payday: bool = True, rearm_margin: float | None = None) -> list[Shock]:
    """Scan daily closing balances and flag each time the runway drops below buffer.

    One shock per dip: after firing, it re-arms only once the projection climbs back above
    buffer + rearm_margin (default: another buffer's worth), so hovering near the line does
    not fire again and again.

    account_id defaults to the primary (most-used) account. Recurring bills and pay default to
    those detected from that account's full history. until_payday=False ignores pay and always
    looks horizon_days ahead.
    """
    txns = list(txns)
    account_id = account_id or primary_account(txns)
    acct = [t for t in txns if t.account_id == account_id]
    if series is None:
        series = detect_recurring_series(acct)
    if income_series is None and until_payday:
        income_series = detect_recurring_series(acct, kind="income")
    if not until_payday:
        income_series = None
    rearm = buffer + (buffer if rearm_margin is None else rearm_margin)
    out, armed = [], True
    for d, bal in end_of_day_balances(txns, account_id):
        p = project_runway(bal, d, series, horizon_days, account_id, income_series)
        if armed and p.projected < buffer:
            out.append(_runway_shock(p, buffer))
            armed = False
        elif not armed and p.projected >= rearm:
            armed = True
    return out


# --------------------------------------------------------------------------------------------
# All together
# --------------------------------------------------------------------------------------------

DETECTORS = {
    ShockType.LARGE_SINGLE_SPEND: detect_large_single_spends,
    ShockType.BURST: detect_bursts,
    ShockType.CATEGORY_SPIKE: detect_category_spikes,
    ShockType.RECURRING_BILL_CHANGE: detect_recurring_bill_changes,
    ShockType.NEW_MERCHANT_BIG_AMOUNT: detect_new_merchant_big_amount,
    ShockType.RUNWAY: detect_runway_shocks,
}


def detect_all_shocks(txns: Iterable[Transaction], options: dict[str, dict] | None = None,
                      only: Iterable[str] | None = None) -> list[Shock]:
    """Run every detector (or just `only`) and return all shocks sorted by date.

    options maps a ShockType to that detector's keyword arguments, e.g.
        {ShockType.LARGE_SINGLE_SPEND: {"floor": 500}, ShockType.RUNWAY: {"buffer": 500}}
    """
    txns = list(txns)
    options = options or {}
    names = list(only) if only is not None else list(DETECTORS)
    shocks = []
    for name in names:
        shocks.extend(DETECTORS[name](txns, **options.get(name, {})))
    return _sorted_by_date(shocks)
