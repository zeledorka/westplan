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

---

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

- `mock_users.json` profiles, demo logins and accounts
- `mock_transactions.json` every transaction, flat list with `user_id`, `account_id`, `category`, `type`, `essential`, `recurring`, `balance_after`
- `statements/<username>.csv` the same data as one CSV statement per customer
- `generate_mock_data.py` regenerates all of the above and `frontend/src/data/mockUsers.json`

```bash
cd backend/data
python generate_mock_data.py
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

```python
from finstats import load_transactions, savings_capacity, detect_all_shocks
txns = load_transactions("data/mock_transactions.json", user_id="u003")
cap = savings_capacity(txns)       # cap.comfortable, cap.stretch, plus C, sigma_W, the W series...
shocks = detect_all_shocks(txns)   # list[Shock] with type, date, amount, score, message
```

```bash
cd backend
python -m finstats                     # demo over all five mock users
python -m unittest discover tests -v   # tests
```

`generate_mock_data.py` plants one shock per customer (fixed values, no extra randomness), and
`tests/test_finstats.py` checks each one is detected:

| Customer | Shock | Planted event |
|---|---|---|
| mia.tran | Burst | 22 Aug 2026: nine small online charges in one day (skimmed card) |
| priya.sharma | Recurring bill change | From Jul 2026: home loan repayment $7,200 to $7,992 (+11%) |
| david.kowalski | Large single spend | 3 Sep 2026: $3,450 gearbox rebuild at his usual mechanic |
| margaret.obrien | New merchant, big amount | 10 Jun 2026: $2,850 at a dental implant clinic |
| jake.morrison | Category spike | Week of 10 Aug 2026: $2,140 of tools over five purchases |

## Financial insights and Gemini-ready context

The additive insights foundation preserves the existing savings and shock APIs. It provides
account-scoped recurring estimates, financial summaries, causal/deduplicated shock events,
explicit reserve handling, allowlisted model input and read-only contribution calculations.
It does not call Gemini or implement the independent Safety Checker.

See [backend/docs/planning-foundation.md](backend/docs/planning-foundation.md) for interfaces,
privacy/timing semantics, the original zero-capacity investigation and integration limits.

```bash
cd backend
python -m unittest discover tests -v
python -m planning.demo examples
```

[Normal example](backend/examples/normal.json) and
[paid veterinary emergency example](backend/examples/vet_paid.json) share one synthetic ledger.
Additional examples cover the pre-event date, an unpaid bill and an anticipated expense.
The original five customers and React placeholder remain unchanged.

## Expanded banking features and persisted goal review

The next phase adds budgets, a reconciled 7/14/30-day bill calendar, savings review evidence,
personalised insights, customer-scoped SQLite feedback/corrections and explicit goal proposal
approval. Simulated subscription states and four constrained read-only search templates are
also available through `planning.service.BankingService`.

See [backend/docs/banking-phase2.md](backend/docs/banking-phase2.md) for public interfaces,
state transitions, model schema 1.1, privacy boundaries and the remaining React/API handoff.
Run `python -m planning.banking_demo examples` from `backend` to regenerate the existing-persona
internal banking and Gemini-context examples. No Gemini call or full Safety Checker is included.

## Run the integrated banking app

The React screens now use the local FastAPI service and existing SQLite-backed banking
facade. See [backend/docs/frontend-integration.md](backend/docs/frontend-integration.md)
for setup, all REST endpoints, customer/scenario isolation, test coverage and AI boundaries.

```bash
# From repository root, terminal 1 (activate .venv first)
python -m pip install -r "westpac-goal-planner 2/backend/requirements.txt"
cd "westpac-goal-planner 2/backend"
python -m uvicorn api:app --host 127.0.0.1 --port 8000

# Terminal 2
cd "westpac-goal-planner 2/frontend"
npm install --cache .runtime/npm-cache
npm run dev
```

Open http://localhost:5173, select a synthetic customer, then use the labelled Demo Scenario
Selector to demonstrate the normal, paid-vet or unpaid-vet shared ledger. Mock selection is
not production authentication. Gemini and the independent checker are not connected; the
UI offers manual goal changes and does not bypass validation for AI proposals.


## Adaptive financial events (context schema 1.2)

The integrated app now separates unusualness, potential cashflow impact and confirmed
expectedness. It monitors regular income interruptions and sustained changes, and stores
customer-scoped event acknowledgements and confirmations in the existing SQLite snapshots.
The original six detector functions and savings calculations remain available unchanged.

See [adaptive-shocks.md](backend/docs/adaptive-shocks.md) for thresholds, lifecycle semantics,
REST actions, privacy changes, evaluation and limitations. Regenerate labelled fixtures with
`python -m evaluation.run_shocks` from `backend`. These synthetic results are not real-world
accuracy estimates. Gemini and the independent Safety Checker remain unimplemented.


## Gemini adaptive goal-planning drafts

The Goal Planner now calls the official backend-only Google Gen AI SDK, using the existing
privacy context v1.2 and read-only Python calculations. Gemini composes strategies and can
refine them through follow-up questions. Results remain `draft_unverified`; the independent
Safety Checker is not implemented, and Gemini drafts cannot enter the approval/activation path.

Configure repository-root `.env` using `.env.example`, then install backend requirements and
restart the API. `GEMINI_MODEL` defaults to `gemini-3.8-flash`. No model call occurs on page load.
See [Gemini planner architecture and API](backend/docs/gemini-planner.md) for limits, privacy,
configuration, endpoints, optional live evaluation and provider-availability results.
