"""Transaction model, loaders for the mock data, and merchant normalisation."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

# Used only when loading CSV statements, which carry no essential flag or type.
DEFAULT_ESSENTIAL_CATEGORIES = frozenset({
    "Groceries", "Housing", "Utilities", "Insurance", "Health", "Transport",
    "Loan repayments", "Kids", "Education", "Tax", "Business expenses",
})


@dataclass(frozen=True)
class Transaction:
    date: date
    description: str
    amount: float              # signed: negative = money out, positive = money in
    category: str
    type: str                  # "expense" | "income" | "transfer"
    essential: bool = False
    recurring: bool = False
    balance_after: float | None = None
    account_id: str = ""
    user_id: str = ""
    transaction_id: str = ""

    @property
    def is_expense(self) -> bool:
        return self.type == "expense"

    @property
    def is_income(self) -> bool:
        return self.type == "income"

    @property
    def spend(self) -> float:
        """Positive dollars spent (0 for money in)."""
        return -self.amount if self.amount < 0 else 0.0

    @property
    def merchant(self) -> str:
        return normalize_merchant(self.description)

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        dt = d["date"]
        if isinstance(dt, str):
            dt = date.fromisoformat(dt[:10])
        return cls(
            date=dt,
            description=d.get("description", ""),
            amount=float(d["amount"]),
            category=d.get("category", ""),
            type=d.get("type") or ("income" if float(d["amount"]) > 0 else "expense"),
            essential=bool(d.get("essential", False)),
            recurring=bool(d.get("recurring", False)),
            balance_after=d.get("balance_after"),
            account_id=d.get("account_id", ""),
            user_id=d.get("user_id", ""),
            transaction_id=d.get("transaction_id", ""),
        )


def load_transactions(path: str | Path, user_id: str | None = None) -> list[Transaction]:
    """Load mock_transactions.json (a flat list), optionally filtered to one user, sorted by date."""
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    txns = [Transaction.from_dict(r) for r in rows if user_id is None or r.get("user_id") == user_id]
    return sort_transactions(txns)


def load_statement_csv(path: str | Path,
                       essential_categories: Iterable[str] = DEFAULT_ESSENTIAL_CATEGORIES) -> list[Transaction]:
    """Load a statements/<username>.csv file. Type and essential are inferred from the category."""
    essential_categories = set(essential_categories)
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        for i, r in enumerate(csv.DictReader(f)):
            debit = float(r["Debit Amount"] or 0)
            credit = float(r["Credit Amount"] or 0)
            amount = credit - debit
            cat = r.get("Categories", "")
            kind = "transfer" if cat == "Transfers" else "income" if amount > 0 else "expense"
            out.append(Transaction(
                date=datetime.strptime(r["Date"], "%d/%m/%Y").date(),
                description=r["Narrative"],
                amount=amount,
                category=cat,
                type=kind,
                essential=kind == "expense" and cat in essential_categories,
                balance_after=float(r["Balance"]) if r.get("Balance") else None,
                account_id=r.get("Bank Account", ""),
                transaction_id=f"csv-{i:05d}",
            ))
    return sort_transactions(out)


def sort_transactions(txns: Iterable[Transaction]) -> list[Transaction]:
    # Stable sort keeps the original order for same-day transactions (needed for balance_after).
    return sorted(txns, key=lambda t: t.date)


_NOISE = re.compile(r"[^A-Z ]+")          # digits, punctuation, store numbers, refs
_SPACES = re.compile(r"\s+")
_PREFIXES = ("SQ *", "SQ*", "PAYPAL *", "PAYPAL*", "EFTPOS ", "VISA PURCHASE ", "DIRECT DEBIT ")


def normalize_merchant(description: str, drop_tokens: Iterable[str] = ()) -> str:
    """Collapse a raw narrative into a stable merchant key.

    "SQ *Rosie's Cafe #0412" -> "ROSIES CAFE". Pass drop_tokens (e.g. suburb names) to remove
    location words so "WOOLWORTHS NEWTOWN" and "WOOLWORTHS MOSMAN" group together.
    """
    s = description.upper().strip()
    for p in _PREFIXES:
        if s.startswith(p):
            s = s[len(p):]
    s = s.replace("'", "")
    s = _SPACES.sub(" ", _NOISE.sub(" ", s)).strip()
    drop = {t.upper() for t in drop_tokens}
    if drop:
        s = " ".join(w for w in s.split() if w not in drop)
    return s


def primary_account(txns: Iterable[Transaction]) -> str:
    """The account with the most expense transactions (the everyday account in the mock data)."""
    counts: dict[str, int] = {}
    for t in txns:
        if t.is_expense:
            counts[t.account_id] = counts.get(t.account_id, 0) + 1
    return max(counts, key=counts.get) if counts else ""
