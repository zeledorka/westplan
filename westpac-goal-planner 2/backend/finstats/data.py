"""Transaction model, loaders for the example data, and merchant normalisation."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


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


STATEMENT_COLUMNS = ("Date", "Account", "Description", "Category", "Type", "Debit ($)", "Credit ($)",
                     "Balance ($)", "Essential", "Recurring", "Transaction ID")
ACCOUNT_SUFFIX = {"Westpac Choice": "everyday", "Westpac eSaver": "savings"}


def load_statement_csv(path: str | Path) -> list[Transaction]:
    """Load a statements/<First>_<Last>.csv file, sorted by date (same-day rows keep file order).

    Columns: see STATEMENT_COLUMNS. Amounts may contain thousands separators ("1,234.56").
    user_id comes from the Transaction ID prefix ("u003-00001" -> "u003") and account_id is
    "<user_id>-everyday" / "<user_id>-savings" (see ACCOUNT_SUFFIX).
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = set(STATEMENT_COLUMNS) - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: not a statement CSV, missing columns {sorted(missing)}")
        return sort_transactions(_from_row(r) for r in reader)


def _money(s: str) -> float:
    s = (s or "").replace(",", "").replace("$", "").strip()
    return float(s) if s else 0.0


def _from_row(r: dict) -> Transaction:
    tid = r["Transaction ID"].strip()
    uid = tid.split("-")[0]
    acct = r["Account"].strip()
    suffix = ACCOUNT_SUFFIX.get(acct, acct.lower().replace(" ", "-"))
    return Transaction(
        date=datetime.strptime(r["Date"].strip(), "%d/%m/%Y").date(),
        description=r["Description"].strip(),
        amount=round(_money(r["Credit ($)"]) - _money(r["Debit ($)"]), 2),
        category=r["Category"].strip(),
        type=r["Type"].strip().lower(),
        essential=r.get("Essential", "").strip().lower() == "yes",
        recurring=r.get("Recurring", "").strip().lower() == "yes",
        balance_after=_money(r["Balance ($)"]) if r.get("Balance ($)", "").strip() else None,
        account_id=f"{uid}-{suffix}",
        user_id=uid,
        transaction_id=tid,
    )


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
