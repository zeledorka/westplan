"""Shared category vocabulary and finite monetary input validation."""
from decimal import Decimal, InvalidOperation

CATEGORIES = frozenset({"Business expenses", "Donations", "Eating out", "Education",
    "Entertainment", "Groceries", "Health", "Housing", "Income", "Insurance", "Kids",
    "Loan repayments", "Shopping", "Subscriptions", "Tax", "Transfers", "Transport",
    "Travel", "Utilities", "Pets", "Other"})


def category(value):
    return value if value in CATEGORIES else "Other"


def money(value, *, signed=False):
    if isinstance(value, bool):
        raise ValueError("Money must be a finite number")
    try:
        n = Decimal(str(value))
        if not n.is_finite() or (not signed and n < 0):
            raise ValueError("Money must be finite and nonnegative")
        return float(n.quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError) as e:
        raise ValueError("Invalid money") from e
