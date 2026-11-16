"""Causal replay and overlapping-alert consolidation around the six existing detectors."""
from datetime import date
from hashlib import sha256
import math
from .bills import recurring_series
from .data import primary_account
from .shocks import DETECTORS, ShockType, check_runway


def normalized_shocks(txns, *, as_of: date):
    from copy import deepcopy
    past=tuple(sorted((t for t in txns if t.date<=as_of),key=lambda t:t.date))
    return deepcopy(_cached_normalized_shocks(past,as_of))


from functools import lru_cache


@lru_cache(maxsize=32)
def _cached_normalized_shocks(txns, as_of):
    txns = sorted((t for t in txns if t.date <= as_of), key=lambda t: t.date)
    ids = [t.transaction_id for t in txns]
    if len(set(ids)) != len(ids) or any(not i for i in ids):
        raise ValueError("Shock normalization requires unique, nonempty transaction IDs")
    # Run each detector against the history known on its evaluation day. Never infer a
    # historical bill or exclusion using a later charge. Category weeks mature over days.
    found = {}
    for day in sorted({t.date for t in txns}):
        past = [t for t in txns if t.date <= day]
        for name, detector in DETECTORS.items():
            if name == ShockType.RUNWAY:
                continue
            for s in detector(past):
                current = s.date == day
                if name == ShockType.CATEGORY_SPIKE:
                    current = 0 <= (day - s.date).days <= 6
                if current:
                    key = (name, s.date, s.details.get("category")) if name == ShockType.CATEGORY_SPIKE else (name, s.date, s.transaction_ids)
                    found[key] = (s, day)
        aid = primary_account(past)
        closing = [t.balance_after for t in past if t.account_id == aid and t.balance_after is not None]
        if closing:
            s = check_runway(closing[-1], day, recurring_series(past, day), account_id=aid,
                             income_series=recurring_series(past, day, "income"))
            if s:
                found[(ShockType.RUNWAY, day)] = (s, day)
    # Connected components by shared transaction IDs. Sum unique posted transactions,
    # never detector amounts (which can represent different quantities).
    groups = []
    for s, observed in found.values():
        matching = [g for g in groups if set(s.transaction_ids) & g["ids"]]
        group = {"ids": set(s.transaction_ids), "alerts": [(s, observed)]}
        for g in matching:
            group["ids"].update(g["ids"])
            group["alerts"].extend(g["alerts"])
            groups.remove(g)
        groups.append(group)
    by_id = {t.transaction_id: t for t in txns}
    out = []
    for g in groups:
        rows = [by_id[i] for i in sorted(g["ids"])]
        alerts = g["alerts"]
        liquidity = not rows
        amount = round(sum(t.spend for t in rows), 2) if rows else None
        event_date = min(t.date for t in rows) if rows else alerts[0][0].date
        types = sorted({s.type for s, _ in alerts})
        # Severity describes evidence/risk, not a diagnosis of hardship.
        shortfall = max((s.score for s, _ in alerts if s.type == ShockType.RUNWAY), default=0)
        out.append({
            "event_ref": sha256((str(event_date) + str(sorted(g["ids"])) + str(types)).encode()).hexdigest()[:16],
            "date": event_date.isoformat(),
            "observed_on": max(d for _, d in alerts).isoformat(),
            "status": "projection" if liquidity else "posted",
            "event_amount": amount,
            "categories": sorted({t.category for t in rows}),
            "detectors": types, "transaction_ids": sorted(g["ids"]),
            "severity": "cashflow_risk" if liquidity else "review",
            "confirmed_hardship": False,
            "classification": "recurring_payment_change" if types == [ShockType.RECURRING_BILL_CHANGE]
                              else "cashflow_projection" if liquidity else "unusual_posted_spending",
            "unexpected_confirmed": False,
            "evidence": [{"detector": s.type, "score": s.score if math.isfinite(s.score) else None,
                          "score_is_unbounded": not math.isfinite(s.score)} for s, _ in alerts],
            "impact": {"already_in_balance": not liquidity,
                       "additional_future_debit": 0,
                       "observed_expense": amount,
                       "projected_buffer_shortfall": round(shortfall, 2) if liquidity else None,
                       "savings_capacity_effect": "Included only when event month is complete; no extra subtraction."},
            "uncertainty": "Statistical anomaly does not establish necessity, surprise, fraud or hardship."})
    return sorted(out, key=lambda e: (e["date"], e["event_ref"]))
