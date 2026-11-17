"""Read-only arithmetic for proposed contributions. Never approves or chooses strategies."""
from decimal import Decimal, ROUND_CEILING
from .schemas import money


def contribution_projection(*, target, allocated, monthly_contribution, months):
    target, allocated, contribution = map(money, (target, allocated, monthly_contribution))
    if isinstance(months, bool) or not isinstance(months, int) or months < 0:
        raise ValueError("months must be a nonnegative integer")
    projected = Decimal(str(allocated)) + months * Decimal(str(contribution))
    remaining = max(Decimal(0), Decimal(str(target)) - projected)
    return {"projected_allocated": float(projected), "remaining": float(remaining),
            "target_reached": remaining == 0, "interest_assumed": 0}


def required_contribution(*, target, allocated, months):
    target, allocated = money(target), money(allocated)
    contribution_projection(target=target, allocated=allocated, monthly_contribution=0, months=months)
    remaining = max(Decimal(0), Decimal(str(target)) - Decimal(str(allocated)))
    if not months:
        return {"monthly_amount": 0 if not remaining else None, "periods": 0}
    return {"monthly_amount": float((remaining / months).quantize(Decimal('.01'), rounding=ROUND_CEILING)),
            "periods": months}


def contribution_cashflow(*, monthly_contribution, monthly_surplus, liquid_balance,
                          protected_reserve=0, pending_expenses=0):
    c, b, reserve, pending = map(money, (monthly_contribution, liquid_balance, protected_reserve, pending_expenses))
    surplus = money(monthly_surplus, signed=True)
    return {"monthly_surplus_after_contribution": round(surplus - c, 2),
            "liquid_after_pending_reserve_and_contribution": round(b - reserve - pending - c, 2),
            "assumption": "Simple proposed one-month contribution; no automatic approval or income guarantee."}
