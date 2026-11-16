# Goal Planner hackathon prototype

## frontend/

A React + Vite mock-up of the banking app shell: Home, Accounts, Payments, Insights and More,
plus a placeholder Goal planner screen (opened from the "Goal planner" card on Home or Insights).

Buttons are for show; only the bottom tabs and the Goal planner entry navigate.
All figures are synthetic and live in `src/data/mockBank.js`.

### Run it

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173 in a browser.

### View it in the Android emulator

1. Start an emulator from Android Studio (Device Manager).
2. Keep `npm run dev` running on your computer.
3. In the emulator, open Chrome and go to http://10.0.2.2:5173
   (10.0.2.2 is the emulator's address for your computer).
4. For a full-screen look, use Chrome's menu, then "Add to Home screen".

On a real Android phone on the same Wi-Fi, use the "Network" address that `npm run dev` prints.

### Where things are

- `src/pages/` one file per screen
- `src/components/ui.jsx` shared pieces (tab bar, cards, tiles)
- `src/components/Icon.jsx` icon set and the logo stand-in
- `src/styles.css` all styling; brand colours are the variables at the top
- `src/pages/GoalPlanner.jsx` where the team's feature mounts

Fictional data only. This is a hackathon prototype, not a Westpac product.

## backend/data/ (synthetic customers)

Five fictional customers, each with one year of transactions (16 Nov 2025 to 15 Nov 2026).

- `statements/<First>_<Last>.csv` **the example data**: one statement per customer with Type, Essential and Recurring columns
- `import_statements.py` rebuilds the JSON below and `frontend/src/data/mockUsers.json` from the statements
- `mock_users.json` profiles, demo logins and accounts
- `mock_transactions.json` every transaction, flat list with `user_id`, `account_id`, `category`, `type`, `essential`, `recurring`, `balance_after`
- `generate_mock_data.py` the original random generator the statements started from. Running it overwrites the JSON with generated data, so run `import_statements.py` afterwards to restore the example data.

```bash
cd backend/data
python import_statements.py   # after editing a statement
```

| Username | Password | Who |
|---|---|---|
| mia.tran | Student#2026 | Student, 20, part-time cafe job, share house |
| priya.sharma | Clinic#2026 | Business owner, 44, home loan + business loan, two kids |
| david.kowalski | Fairway#2026 | Middle income, 47, smaller mortgage, no kids |
| margaret.obrien | Garden#2026 | Age pensioner, 72, renting, no property |
| jake.morrison | Sparky#2026 | Electrician with an ABN, 29, share house |

The sign-in screen is a demo-only plain-text check against these mock users. Tap a user on that screen to fill in their details. The profile icon signs out.

## backend/finstats/ (savings and shock helpers)

Standard library only (Python 3.9+). Formulas and detector rules are documented at the top of
`savings.py` and `shocks.py`; every threshold is a keyword argument.

Shock types: large single spend, burst, category spike, recurring bill change, new merchant
with a big amount, runway (bills due before next pay), overdraft, savings drawdown (topping up
the everyday account from savings) and buy-now-pay-later stacking (3+ plans at once, or
repayments over 10% of a month's income).

```python
from finstats import load_statement_csv, savings_capacity, detect_all_shocks
txns = load_statement_csv("data/statements/David_Kowalski.csv")
cap = savings_capacity(txns)       # cap.comfortable, cap.stretch, plus C, sigma_W, the W series...
shocks = detect_all_shocks(txns)   # list[Shock] with type, date, amount, score, message
```

```bash
cd backend
python -m finstats                     # demo over all five mock users
python -m unittest discover tests -v   # tests
```

The example statements include planted shock events (transaction IDs like `u003-S0001`).
`tests/test_finstats.py` checks every one is flagged by at least one detector:

| Customer | Planted events | Flagged as |
|---|---|---|
| Mia Tran | Vet $680 (5 Mar), phone repair $329 (14 Jul) | New merchant; large single spend + new merchant |
| Priya Sharma | Emergency dentist $1,850 (9 Feb), car repair $2,380 (18 Jun), plumber $2,150 (3 Sep) | New merchant; large single spend + new merchant; new merchant |
| David Kowalski | Vet $1,240 (2 Apr), car service $1,860 (12 Aug) | Large single spend + new merchant (both) |
| Margaret O'Brien | Fridge $1,095 (20 Jan), dentist $740 (8 Jul) | New merchant; large single spend + new merchant |
| Jake Morrison | Car service $2,740 (26 Feb), tools $1,990 (27 Aug), windscreen $620 (20 Oct) | Large single spend + new merchant; large single spend; large single spend + new merchant |
