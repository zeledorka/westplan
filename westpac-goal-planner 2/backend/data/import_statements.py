"""Rebuild the JSON mock data from the example statements.

The example customer data lives in statements/<First>_<Last>.csv (columns: Date, Account,
Description, Category, Type, Debit ($), Credit ($), Balance ($), Essential, Recurring,
Transaction ID). Edit those, then run (from backend/data):

    python import_statements.py

Writes, next to this file:
    mock_transactions.json   every transaction for every user (flat list)
    mock_users.json          account opening/closing balances updated (profiles kept)
and, for the frontend:
    ../../frontend/src/data/mockUsers.json   the per-user summary the app shell displays
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from finstats import load_statement_csv  # noqa: E402


def load_all():
    by_user = defaultdict(list)
    for path in sorted((HERE / "statements").glob("*.csv")):
        try:
            txns = load_statement_csv(path)
        except ValueError as e:
            print(f"skipping {e}")
            continue
        for t in txns:
            by_user[t.user_id].append(t)
    return by_user


def as_row(t):
    return dict(date=t.date.isoformat(), description=t.description, amount=t.amount, category=t.category,
                type=t.type, essential=t.essential, recurring=t.recurring, balance_after=t.balance_after,
                transaction_id=t.transaction_id, user_id=t.user_id, account_id=t.account_id)


def run():
    users = json.loads((HERE / "mock_users.json").read_text())
    by_user = load_all()
    all_rows, front = [], []
    for u in users:
        txns = by_user.get(u["user_id"])
        if not txns:
            print(f"{u['username']:16} no statement found, left unchanged")
            continue
        rows = [as_row(t) for t in txns]
        all_rows += rows

        for a in u["accounts"]:
            acct = [t for t in txns if t.account_id == a["account_id"]]
            if acct:
                a["opening_balance"] = round(acct[0].balance_after - acct[0].amount, 2)
                a["balance"] = round(acct[-1].balance_after, 2)

        # summary for the app shell, same rules as generate_mock_data.py
        end = max(t.date for t in txns)
        month_start = end.replace(day=1)
        mtd = [t for t in txns if t.date >= month_start and t.type != "transfer"]
        spent = -sum(t.amount for t in mtd if t.amount < 0)
        earned = sum(t.amount for t in mtd if t.amount > 0)
        cash = sum(a["balance"] for a in u["accounts"] if a["type"] != "loan")
        debt = -sum(a["balance"] for a in u["accounts"] if a["type"] == "loan")
        recent = [r for r in rows if r["account_id"].endswith("everyday")][-12:][::-1]
        front.append(dict(
            id=u["user_id"], username=u["username"], password=u["password"], firstName=u["first_name"],
            persona=u["persona"],
            accounts=[dict(id=a["account_id"], name=a["name"], balance=a["balance"],
                           kind="loan" if a["type"] == "loan" else "cash", cardEnding=a.get("card_ending"))
                      for a in u["accounts"]],
            cashFlow=dict(since=month_start.strftime("%d %b"), expenses=round(spent, 2), income=round(earned, 2)),
            categoriesTotal=round(spent, 2),
            netWorth=dict(assets=round(cash + u.get("property_value", 0), 2), liabilities=round(debt, 2)),
            recent=[dict(date=r["date"], description=r["description"], amount=r["amount"]) for r in recent]))
        print(f"{u['username']:16} tx={len(rows):5}  everyday end={u['accounts'][0]['balance']:10,.2f}")

    (HERE / "mock_users.json").write_text(json.dumps(users, indent=2))
    (HERE / "mock_transactions.json").write_text(json.dumps(all_rows, indent=1))
    fe = HERE / "../../frontend/src/data"
    if fe.exists():
        (fe / "mockUsers.json").write_text(json.dumps(front, indent=1))
    print("total transactions:", len(all_rows))


if __name__ == "__main__":
    run()
