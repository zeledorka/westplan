"""finstats: savings-capacity and spending-shock helpers. Standard library only.

    from finstats import load_statement_csv, savings_capacity, detect_all_shocks
    txns = load_statement_csv("data/statements/David_Kowalski.csv")
    cap = savings_capacity(txns)          # cap.comfortable, cap.stretch, plus every intermediate
    shocks = detect_all_shocks(txns)      # list[Shock], sorted by date
"""
from .data import (
    STATEMENT_COLUMNS, Transaction, load_statement_csv, load_transactions, normalize_merchant,
    primary_account, sort_transactions,
)
from .savings import (
    MonthlyFlow, SavingsCapacity, monthly_flows, monthly_net, savings_capacity,
    savings_capacity_from_flows, smoothed_net,
)
from .shocks import (
    BNPL_PROVIDERS, DETECTORS, BnplPlan, RecurringSeries, RunwayProjection, Shock, ShockType,
    bnpl_plans, check_runway, detect_all_shocks, detect_bnpl_stacking, detect_bursts,
    detect_category_spikes, detect_large_single_spends, detect_new_merchant_big_amount,
    detect_overdrafts, detect_recurring_bill_changes, detect_recurring_series,
    detect_runway_shocks, detect_savings_drawdowns, end_of_day_balances, is_bnpl, next_payday,
    project_runway, upcoming_bills, week_start,
)
from .stats import ewma, mad, mean, median, modified_z, quantile, rolling_mean, std

from .insights import financial_insights
from .bills import bill_insights, recurring_series
from .shock_context import normalized_shocks, expectedness_for
from .income_shocks import detect_income_interruptions
from .pattern_shocks import detect_persistent_changes

from .banking import banking_insights
from .budgets import BudgetLimit, budget_summary
from .bills import bills_calendar
from .categorisation import Classification, classify_transaction, classified_transactions
from .opportunities import find_savings_opportunities
from .personalised_insights import personalised_insights
from .search import TransactionQuery, parse_question, search_transactions

__all__ = sorted(k for k in list(globals()) if not k.startswith("_") and k not in {"data", "savings", "shocks", "stats", "insights", "bills", "opportunities", "shock_context", "banking", "budgets", "categorisation", "contracts", "search", "event_model", "event_lifecycle", "income_shocks", "pattern_shocks"})
