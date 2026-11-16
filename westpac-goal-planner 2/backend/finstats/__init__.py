"""finstats: savings-capacity and spending-shock helpers. Standard library only.

    from finstats import load_transactions, savings_capacity, detect_all_shocks
    txns = load_transactions("data/mock_transactions.json", user_id="u003")
    cap = savings_capacity(txns)          # cap.comfortable, cap.stretch, plus every intermediate
    shocks = detect_all_shocks(txns)      # list[Shock], sorted by date
"""
from .data import (
    DEFAULT_ESSENTIAL_CATEGORIES, Transaction, load_statement_csv, load_transactions,
    normalize_merchant, primary_account, sort_transactions,
)
from .savings import (
    MonthlyFlow, SavingsCapacity, centre, monthly_flows, monthly_net, savings_capacity,
    savings_capacity_from_flows, smoothed_net,
)
from .shocks import (
    DETECTORS, RecurringSeries, RunwayProjection, Shock, ShockType, check_runway, next_payday, project_runway, detect_all_shocks, detect_bursts,
    detect_category_spikes, detect_large_single_spends, detect_new_merchant_big_amount,
    detect_recurring_bill_changes, detect_recurring_series, detect_runway_shocks,
    end_of_day_balances, score_single_spend, upcoming_bills, weekly_category_totals,
)
from .stats import ewma, mad, mean, median, modified_z, quantile, rolling_mean, std

__all__ = sorted(k for k in list(globals()) if not k.startswith("_") and k not in {"data", "savings", "shocks", "stats"})
