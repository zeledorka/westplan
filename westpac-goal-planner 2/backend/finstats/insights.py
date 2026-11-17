"""Unified banking facts and explicitly labelled estimates. No goal strategies."""
from dataclasses import asdict
from datetime import date
import math
from .data import primary_account
from .savings import monthly_flows, savings_capacity
from .stats import mean, std
from .bills import bill_insights, recurring_series
from .shocks import project_runway
from .opportunities import spending_opportunities


def financial_insights(txns, *, as_of: date, accounts=(), protected_categories=()):
    txns = sorted((t for t in txns if t.date <= as_of), key=lambda t: t.date)
    for t in txns:
        if isinstance(t.amount, bool) or not math.isfinite(t.amount):
            raise ValueError("Transactions require finite amounts")
        if t.balance_after is not None and not math.isfinite(t.balance_after):
            raise ValueError("Transactions require finite balances")
    users = {t.user_id for t in txns if t.user_id}
    if len(users) > 1:
        raise ValueError("Provide one customer's transactions")
    accounts = [a for a in accounts if a.as_of <= as_of]
    if len({a.account_id for a in accounts}) != len(accounts):
        raise ValueError("Duplicate account snapshot")
    flows = monthly_flows(txns)
    cap = savings_capacity(txns)
    full_months = {f.month for f in flows}
    full = [t for t in txns if t.date.strftime("%Y-%m") in full_months]
    n = len(flows)
    income_series = recurring_series(txns, as_of, "income")
    regular_ids = {id(t) for s in income_series for t in s.occurrences}
    income_total = sum(t.amount for t in full if t.is_income)
    regular_total = sum(t.amount for t in full if t.is_income and id(t) in regular_ids)
    incomes = [f.income for f in flows]
    cv = std(incomes) / mean(incomes) if mean(incomes) > 0 else None
    cats = []
    for cat in sorted({t.category for t in full if t.is_expense}):
        rows = [t for t in full if t.is_expense and t.category == cat]
        recent = sum(-t.amount for t in rows if t.date.strftime("%Y-%m") == flows[-1].month)
        prior = sum(-t.amount for t in rows if t.date.strftime("%Y-%m") != flows[-1].month)
        cats.append({"category": cat, "monthly_total": round(sum(-t.amount for t in rows) / n, 2),
                     "monthly_discretionary": round(sum(-t.amount for t in rows if not t.essential) / n, 2),
                     "recent_change": round(recent - prior / (n - 1), 2) if n > 1 else None})
    balances = []
    snapshots = {a.account_id: a for a in accounts if a.as_of <= as_of}
    for aid in sorted(set(snapshots) | {t.account_id for t in txns}):
        a = snapshots.get(aid)
        known = [t for t in txns if t.account_id == aid and t.balance_after is not None
                 and (a is None or t.date >= a.as_of)]
        balance = float(known[-1].balance_after) if known else a.balance if a else None
        protected = a.protected if a else 0
        designation = a.goal_available if a else 0
        balances.append({"account_id": aid, "kind": a.kind if a else "unknown",
                         "balance": balance,
                         "observed_on": known[-1].date.isoformat() if known else a.as_of.isoformat() if a else None,
                         "protected": protected,
                         "goal_available": round(min(designation, max(0, (balance or 0) - protected)), 2),
                         "designation_shortfall": round(max(0, protected + designation - max(0, balance or 0)), 2)})
    bills = bill_insights(txns, as_of)
    acct = primary_account(txns)
    balance = next((a["balance"] for a in balances if a["account_id"] == acct), None)
    runway = None
    if balance is not None:
        p = project_runway(balance, as_of, recurring_series(txns, as_of), account_id=acct,
                           income_series=income_series)
        runway = {"account_id": acct, "balance": p.balance, "recurring_bills_due": p.bills_due,
                  "projected_balance": p.projected, "window_end": p.window_end.isoformat(),
                  "payday": p.payday.isoformat() if p.payday else None,
                  "below_default_buffer": p.projected < 200, "buffer": 200}
    recent = mean(incomes[-3:]) if incomes else None
    older = mean(incomes[:-3]) if len(incomes) > 3 else None
    return {
        "as_of": as_of.isoformat(), "currency": "AUD",
        "observation": {"first": txns[0].date.isoformat() if txns else None,
                        "last": txns[-1].date.isoformat() if txns else None,
                        "complete_months": n, "transactions": len(txns)},
        "income": {"monthly_average": round(mean(incomes), 2),
                   "regular_monthly_estimate": round(regular_total / n, 2) if n else None,
                   "irregular_monthly_observed": round((income_total - regular_total) / n, 2) if n else None,
                   "variability_cv": round(cv, 4) if cv is not None else None,
                   "recent_three_month_average": round(recent, 2) if recent is not None else None,
                   "change_vs_earlier_months": round(recent - older, 2) if older is not None else None,
                   "confidence": "limited" if n < 6 else "moderate" if cv is None or cv > .2 else "higher",
                   "confidence_basis": "History length and monthly variability; not a calibrated probability."},
        "spending": {"monthly_essential": round(mean([f.essential for f in flows]), 2),
                     "monthly_discretionary": cap.mean_discretionary, "categories": cats},
        "cashflow": {"monthly": [{**asdict(f), "net": round(f.net, 2)} for f in flows],
                     "average_surplus": round(mean([f.net for f in flows]), 2),
                     "month_to_date_net": round(sum(t.amount for t in txns if t.type != "transfer"
                         and t.date.replace(day=1) == as_of.replace(day=1)), 2),
                     "savings_capacity": {k: v for k, v in asdict(cap).items() if k not in {"months", "smoothed"}},
                     "runway": runway},
        "recurring": bills, "balances": balances,
        "opportunities": spending_opportunities(cats, bills, protected_categories),
        "uncertainty": ["Complete months inferred from transaction boundary dates, not statement coverage.",
                        "Recurring dates/amounts and regular income are estimates from observed history.",
                        "Source recurring flags are not confirmation of a contract.",
                        "Runway includes recurring bills only; excludes ordinary future spending and uncertain income.",
                        "Savings capacity excludes the incomplete current month and is not a liquidity guarantee.",
                        "Savings balances are unavailable for goals unless explicitly designated.",
                        "Loan balances and protected reserves require dated external snapshots."]}
