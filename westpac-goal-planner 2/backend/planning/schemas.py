"""Explicit inputs to the insights/planning boundary. Money is AUD, not account metadata."""
from dataclasses import dataclass
from datetime import date
import math

from finstats.contracts import CATEGORIES, category, money


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    kind: str  # transaction, savings, loan
    balance: float
    as_of: date
    protected: float = 0  # explicit restriction, not inferred from account names
    goal_available: float = 0  # explicitly designated savings pool, INCLUDING existing goal allocations

    def __post_init__(self):
        if self.kind not in {"transaction", "savings", "loan"}:
            raise ValueError("Unknown account kind")
        object.__setattr__(self, "balance", money(self.balance, signed=True))
        object.__setattr__(self, "protected", money(self.protected))
        object.__setattr__(self, "goal_available", money(self.goal_available))
        if self.protected + self.goal_available > max(0, self.balance):
            raise ValueError("Designations exceed snapshot balance")


@dataclass(frozen=True)
class Goal:
    goal_id: str  # internal reference; never sent to Gemini
    kind: str
    target: float
    allocated: float
    deadline: date

    def __post_init__(self):
        if self.kind not in {"holiday", "emergency_fund", "purchase", "other"}:
            raise ValueError("Use a supported goal kind; keep personal descriptions outside model input")
        object.__setattr__(self, "target", money(self.target))
        object.__setattr__(self, "allocated", money(self.allocated))
        if self.allocated > self.target:
            raise ValueError("Allocation exceeds target")


@dataclass(frozen=True)
class Preferences:
    protected_categories: tuple[str, ...] = ()
    allow_deadline_extension: bool = True
    allow_discretionary_changes: bool = True

    def __post_init__(self):
        if not isinstance(self.allow_deadline_extension, bool) or not isinstance(self.allow_discretionary_changes, bool):
            raise ValueError("Preference switches must be booleans")
        if any(c not in CATEGORIES for c in self.protected_categories):
            raise ValueError("Unknown protected category")


@dataclass(frozen=True)
class PendingExpense:
    event_id: str
    amount: float
    category: str
    known_on: date
    due: date
    status: str = "unpaid"  # unpaid = known obligation; anticipated = uncertain future expense
    transaction_id: str | None = None  # if posted, use the transaction instead
    replaces_bill_id: str | None = None  # explicit reconciliation against an inferred occurrence

    def __post_init__(self):
        object.__setattr__(self, "amount", money(self.amount))
        if self.status not in {"unpaid", "anticipated"}:
            raise ValueError("Posted expenses belong in transactions")
        if self.due < self.known_on:
            raise ValueError("Due date precedes knowledge date")


def finite_score(value):
    return value if math.isfinite(value) else None


@dataclass(frozen=True)
class PostedEventEvidence:
    """Explicit user/scenario evidence; never inferred from merchant narratives."""
    transaction_id: str
    known_on: date
    kind: str = "unexpected_expense"
    unexpected: bool = True

    def __post_init__(self):
        if self.kind not in {"emergency_veterinary", "unexpected_expense", "other"}:
            raise ValueError("Unsupported event kind")
        if not isinstance(self.unexpected, bool):
            raise ValueError("unexpected must be a boolean")
