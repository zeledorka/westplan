"""Reproducible synthetic holiday/vet scenario. python -m planning.demo [output_directory]."""
from dataclasses import replace
from datetime import date
from pathlib import Path
import sys
from finstats.data import Transaction
from .schemas import AccountSnapshot, Goal, Preferences, PendingExpense, PostedEventEvidence
from .context import build_planning_context, context_json

AS_OF = date(2026, 9, 30)


def demo_transactions(include_vet=False):
    rows = []
    def add(day, description, amount, cat, essential=False, recurring=False,
            account="demo-everyday", kind=None):
        rows.append(Transaction(day, description, amount, cat,
            kind or ("income" if amount > 0 else "expense"), essential, recurring,
            account_id=account, user_id="synthetic-holiday"))
    for month in range(1, 10):
        def d(day):
            return date(2026, month, day)
        add(d(1), "DEMO PAYROLL", 4000, "Income", recurring=True)
        for day, desc, amount, cat, essential in [
            (2, "DEMO RENT", 1600, "Housing", True),
            (6, "DEMO UTILITIES", 200, "Utilities", True),
            (10, "DEMO TRANSPORT PASS", 200, "Transport", True),
            (12, "DEMO HEALTH", 100, "Health", True),
            (15, "DEMO DINING", 300, "Eating out", False),
            (20, "DEMO ENTERTAINMENT", 180, "Entertainment", False),
            (22, "DEMO SUBSCRIPTION", 20, "Subscriptions", False)]:
            add(d(day), desc, -amount, cat, essential, True)
        for day in (7, 14, 21, 28):
            add(d(day), "DEMO GROCER", -125, "Groceries", True)
        # Transfers redistribute existing surplus; they are not another expense.
        transfer_day = date(2026, month, 28 if month == 2 else 30)
        add(transfer_day, "TRANSFER TO SAVINGS", -900, "Transfers", account="demo-everyday", kind="transfer")
        add(transfer_day, "TRANSFER FROM EVERYDAY", 900, "Transfers", account="demo-savings", kind="transfer")
    rows.sort(key=lambda t: t.date)
    if include_vet:
        add(AS_OF, "DEMO EMERGENCY VETERINARY CLINIC", -1850, "Pets", True)
    balances = {"demo-everyday": 2000, "demo-savings": 1200}
    out = []
    for i, t in enumerate(rows):
        balances[t.account_id] = round(balances[t.account_id] + t.amount, 2)
        out.append(replace(t, balance_after=balances[t.account_id], transaction_id=f"demo-{i:04d}"))
    return out


def demo_contexts():
    # Only a designated $1,200 is allocated to the holiday. The other savings are
    # explicitly protected for a separate obligation in this synthetic scenario.
    accounts = (AccountSnapshot("demo-everyday", "transaction", 2000, date(2026, 1, 1)),
                AccountSnapshot("demo-savings", "savings", 9300, AS_OF, protected=8100, goal_available=1200))
    args = dict(as_of=AS_OF, accounts=accounts,
                goals=(Goal("holiday-internal", "holiday", 6000, 1200, date(2027, 5, 31)),),
                preferences=Preferences(protected_categories=("Health", "Pets")))
    before = dict(args, as_of=date(2026, 9, 29), accounts=(accounts[0],
        AccountSnapshot("demo-savings", "savings", 8400, date(2026, 9, 29), protected=7200, goal_available=1200)))
    return {"before_event": build_planning_context(demo_transactions(True), **before),
            "normal": build_planning_context(demo_transactions(), **args),
            "vet_paid": build_planning_context(demo_transactions(True), **args,
                event_evidence=(PostedEventEvidence(demo_transactions(True)[-1].transaction_id,
                                                   AS_OF, "emergency_veterinary"),)),
            "vet_unpaid": build_planning_context(demo_transactions(), **args,
                pending_expenses=(PendingExpense("vet", 1850, "Pets", AS_OF, AS_OF),)),
            "vet_anticipated": build_planning_context(demo_transactions(), **args,
                pending_expenses=(PendingExpense("vet", 1850, "Pets", AS_OF, AS_OF, "anticipated"),))}


def main():
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "examples")
    target.mkdir(parents=True, exist_ok=True)
    for name, context in demo_contexts().items():
        (target / f"{name}.json").write_text(context_json(context) + "\n", encoding="utf-8")
        facts = context["financial_insights"]["cashflow"]
        print(name, "comfortable", facts["savings_capacity"]["comfortable"],
              "runway", facts["runway"]["projected_after_known_pending"])


if __name__ == "__main__":
    main()
