"""Demo: python -m finstats [user_id ...]   (run from backend/)

Prints savings capacity and a shock summary for each mock user.
"""
import sys
from collections import Counter
from pathlib import Path

from . import detect_all_shocks, load_transactions, savings_capacity

DATA = Path(__file__).resolve().parent.parent / "data" / "mock_transactions.json"


def main(argv: list[str]) -> None:
    users = argv or ["u001", "u002", "u003", "u004", "u005"]
    for uid in users:
        txns = load_transactions(DATA, user_id=uid)
        cap = savings_capacity(txns)
        shocks = detect_all_shocks(txns)
        print(f"== {uid}: {len(txns)} transactions, {len(cap.months)} full months")
        print(f"   income ${cap.mean_income:,.0f}/mo, discretionary ${cap.mean_discretionary:,.0f}/mo")
        print(f"   C ${cap.centre:,.0f} (EWMA ${cap.ewma_w:,.0f}, mean ${cap.mean_w:,.0f}), "
              f"sigma_W ${cap.sigma_w:,.0f}, cap ${cap.income_cap:,.0f}")
        print(f"   comfortable ${cap.comfortable:,.2f}/mo   stretch ${cap.stretch:,.2f}/mo")
        print(f"   shocks: {dict(Counter(s.type for s in shocks))}")
        for s in shocks[-5:]:
            print(f"     {s.date} {s.type}: {s.message}")


if __name__ == "__main__":
    main(sys.argv[1:])
