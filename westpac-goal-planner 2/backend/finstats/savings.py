"""Safe monthly savings capacity.

    N_t = I_t - E_t - D_t                    monthly net (expenses by Type = expense; transfers excluded)
    W_t = (N_t + N_{t-1} + N_{t-2}) / 3      3-month smoothed net
    C   = min(EWMA(W), mean(W))              centre: recent-weighted, never above the long-run average
    sigma_W = std(W)                         volatility of the smoothed series

    Comfortable:  B  = min(gamma * I_bar, max(0, C - sigma_W))
    Stretch:      S* = min(gamma * I_bar, max(0, C - sigma_W + alpha * D_bar))

I = income, E = essential expenses, D = discretionary (non-essential) expenses.
Stretch assumes the person could trim a share alpha of their average discretionary spend.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

from .data import Transaction
from .categorisation import committed_transactions
from .stats import ewma, mean, rolling_mean, std


@dataclass(frozen=True)
class MonthlyFlow:
    month: str          # "YYYY-MM"
    income: float       # I_t
    essential: float    # E_t (positive dollars)
    discretionary: float  # D_t (positive dollars)

    @property
    def net(self) -> float:  # N_t
        return self.income - self.essential - self.discretionary


@dataclass(frozen=True)
class SavingsCapacity:
    comfortable: float     # B
    stretch: float         # S*
    centre: float          # C
    ewma_w: float
    mean_w: float
    sigma_w: float
    income_cap: float      # gamma * I_bar
    mean_income: float     # I_bar
    mean_discretionary: float  # D_bar
    months: list[MonthlyFlow] = field(default_factory=list)
    smoothed: list[float] = field(default_factory=list)  # W series
    params: dict = field(default_factory=dict)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def monthly_flows(txns: Iterable[Transaction], complete_months_only: bool = True,
                  account_ids: Iterable[str] | None = None) -> list[MonthlyFlow]:
    """Group transactions into calendar months of I_t, E_t, D_t, sorted by month.

    Transfers are ignored. Refunds (positive amounts on expenses) reduce that month's spend.
    With complete_months_only, the first/last month is dropped when the data does not cover it
    from day 1 / to its last day (e.g. a statement running 16 Nov to 15 Nov).
    Months with no transactions inside the covered range are kept as zeros.
    """
    accts = set(account_ids) if account_ids is not None else None
    txns = committed_transactions([t for t in txns if accts is None or t.account_id in accts])
    if not txns:
        return []
    first, last = min(t.date for t in txns), max(t.date for t in txns)

    buckets: dict[str, list[float]] = {}
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        buckets[f"{y:04d}-{m:02d}"] = [0.0, 0.0, 0.0]
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    for t in txns:
        b = buckets[_month_key(t.date)]
        if t.is_income:
            b[0] += t.amount
        elif t.is_expense:
            b[1 if t.essential else 2] += -t.amount

    if complete_months_only:
        if first.day != 1:
            buckets.pop(_month_key(first), None)
        if last.day != calendar.monthrange(last.year, last.month)[1]:
            buckets.pop(_month_key(last), None)

    return [MonthlyFlow(k, round(v[0], 2), round(v[1], 2), round(v[2], 2)) for k, v in sorted(buckets.items())]


def monthly_net(flows: Iterable[MonthlyFlow]) -> list[float]:
    """N_t series."""
    return [f.net for f in flows]


def smoothed_net(net: list[float], window: int = 3) -> list[float]:
    """W_t series: trailing `window`-month mean of N_t. Shorter than net by window-1."""
    if len(net) < window:
        return [mean(net)] if net else []
    return rolling_mean(net, window)


def savings_capacity(txns: Iterable[Transaction], gamma: float = 0.30, alpha: float = 0.20,
                     window: int = 3, ewma_span: float = 6, ddof: int = 1,
                     complete_months_only: bool = True,
                     account_ids: Iterable[str] | None = None) -> SavingsCapacity:
    """Comfortable (B) and stretch (S*) monthly savings, with every intermediate value.

    gamma  cap as a share of average monthly income (never suggest saving more than this)
    alpha  share of average discretionary spend assumed trimmable for the stretch figure
    """
    flows = monthly_flows(txns, complete_months_only, account_ids)
    return savings_capacity_from_flows(flows, gamma, alpha, window, ewma_span, ddof)


def savings_capacity_from_flows(flows: list[MonthlyFlow], gamma: float = 0.30, alpha: float = 0.20,
                                window: int = 3, ewma_span: float = 6, ddof: int = 1) -> SavingsCapacity:
    params = dict(gamma=gamma, alpha=alpha, window=window, ewma_span=ewma_span, ddof=ddof)
    if not flows:
        return SavingsCapacity(0, 0, 0, 0, 0, 0, 0, 0, 0, [], [], params)

    w = smoothed_net(monthly_net(flows), window)
    ew, mw = ewma(w, span=ewma_span), mean(w)
    c = min(ew, mw)
    sigma = std(w, ddof)
    i_bar = mean([f.income for f in flows])
    d_bar = mean([f.discretionary for f in flows])
    cap = gamma * i_bar

    comfortable = min(cap, max(0.0, c - sigma))
    stretch = min(cap, max(0.0, c - sigma + alpha * d_bar))

    return SavingsCapacity(
        comfortable=round(comfortable, 2), stretch=round(stretch, 2), centre=round(c, 2),
        ewma_w=round(ew, 2), mean_w=round(mw, 2), sigma_w=round(sigma, 2),
        income_cap=round(cap, 2), mean_income=round(i_bar, 2), mean_discretionary=round(d_bar, 2),
        months=flows, smoothed=[round(x, 2) for x in w], params=params,
    )
