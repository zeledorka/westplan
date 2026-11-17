"""User limits versus observed calendar-month spending; remaining is not liquid cash."""
from dataclasses import dataclass
from datetime import date
from .contracts import CATEGORIES, money


@dataclass(frozen=True)
class BudgetLimit:
    category: str
    monthly_limit: float
    protected: bool = False
    source: str = "user_defined"

    def __post_init__(self):
        if self.category not in CATEGORIES or self.category in {"Income", "Transfers"}:
            raise ValueError("Budget category must be an expense category")
        if self.source not in {"user_defined", "inferred", "demo_default"}:
            raise ValueError("Unknown budget provenance")
        if not isinstance(self.protected, bool):
            raise ValueError("protected must be boolean")
        object.__setattr__(self, 'monthly_limit', money(self.monthly_limit))


def budget_summary(txns, *, as_of: date, limits=(), protected_categories=(), calendar=None):
    if len({b.category for b in limits}) != len(limits):
        raise ValueError("Duplicate category budget")
    rows = [t for t in txns if t.date <= as_of and t.date.replace(day=1) == as_of.replace(day=1)
            and t.is_expense]
    by_limit = {b.category: b for b in limits}
    categories = sorted({t.category for t in rows} | set(by_limit) | {b['category'] for b in (calendar or {}).get('items',[]) if b['status']=='upcoming'})
    out = []
    for cat in categories:
        b = by_limit.get(cat)
        actual = [t for t in rows if t.category == cat]
        spent = round(sum(-t.amount for t in actual), 2)
        limit = b.monthly_limit if b else None
        status = 'unbudgeted' if b is None else 'over_budget' if spent > limit else (
            'approaching_limit' if spent >= .8 * limit and (limit > 0 or spent > 0) else 'within_limit')
        recurring = sum(x['expected_amount'] for x in (calendar or {}).get('items', [])
            if x['category'] == cat and x['status'] == 'upcoming'
            and x['expected_date'][:7] == as_of.strftime('%Y-%m'))
        out.append({'category': cat, 'monthly_limit': limit, 'spent': spent,
                    'essential_spent': round(sum(-t.amount for t in actual if t.essential), 2),
                    'discretionary_spent': round(sum(-t.amount for t in actual if not t.essential), 2),
                    'remaining': round(limit-spent, 2) if b else None,
                    'protected': cat in protected_categories or bool(b and b.protected),
                    'source': b.source if b else 'unset', 'status': status,
                    'upcoming_obligations_this_month': round(recurring, 2),
                    'remaining_is_cash_available': False})
    return {'month': as_of.strftime('%Y-%m'), 'as_of': as_of.isoformat(), 'categories': out,
            'approaching_threshold': .8, 'assumption': 'Monthly budget remaining is not available cash.'}
