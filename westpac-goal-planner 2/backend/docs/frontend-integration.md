# React / FastAPI banking integration

The existing React banking shell now reads the existing Python banking service through a local FastAPI adapter. Financial calculations remain in Python. No Gemini, full independent Safety Checker, real bank/payment API, real subscription cancellation or Tax Organiser is implemented.

## Start locally

Backend, in terminal 1:

```bash
cd "/Users/s5285057/Documents/code/westplan"
source .venv/bin/activate
python -m pip install -r "westpac-goal-planner 2/backend/requirements.txt"
cd "westpac-goal-planner 2/backend"
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

Frontend, in terminal 2:

```bash
cd "/Users/s5285057/Documents/code/westplan/westpac-goal-planner 2/frontend"
npm install --cache .runtime/npm-cache
npm run dev
```

Open http://localhost:5173. Interactive REST documentation is at http://127.0.0.1:8000/docs. Vite proxies `/api` to the loopback backend, so browser requests use the same origin. Port 5173 is strict to keep the allowed development origin predictable.

The checked-in `node_modules` came from Windows and its Vite executable was not executable on this Mac. npm scripts now invoke Vite through Node directly. Production validation used matching Darwin native Rollup/esbuild packages installed in a separate workspace tooling directory, preserving tracked dependencies. Running the frontend install command above resolves native packages for the current machine. The explicit npm cache avoids this machine's unwritable home cache. Vite's generated cache is under ignored `frontend/.runtime/`, not tracked `node_modules/.vite`.

Persistence defaults to `backend/.runtime/banking.sqlite`; override with `BANKING_DB`. `.env`, virtual environments, Python bytecode, runtime data and build outputs are ignored. The service never reads the Gemini key or sends it to the browser. Do not run with multiple workers: demo sessions live in one process.

## Connected screens

- **Customer selection:** backend-supplied synthetic personas, no browser-bundled passwords. Selection establishes a server-held mock session.
- **Home:** backend account balances, month-to-date income/expenses/net, spending entry point, savings/cashflow estimates and personalised insights. Net-worth values are calculated in Python using synthetic account/property metadata.
- **Accounts:** selected-customer categorised transactions, confidence/source, uncertainty flags and persisted correction forms. Up to 200 recent rows are shown; the REST list is bounded to 1,000.
- **Insights & tools:** functional entry points to budgets, bills, savings opportunities, subscriptions, constrained search and spending breakdown. Insight feedback persists and critical notices remain visible.
- **Budget Planner:** set/edit explicit limits and protection, observed essential/discretionary spending, signed remaining amounts and threshold status. Remaining budget is not available cash.
- **Bills Calendar:** 7/14/30-day confirmed/estimated/anticipated totals, upcoming/outstanding obligations, and a separate latest-paid section. Dates remain labelled as estimates unless explicitly confirmed.
- **Savings Finder:** observed evidence, conditional monthly ranges and preference/protection actions. Viewing an opportunity changes no budget.
- **Subscriptions:** simulated request, failed and confirmed cancellation states. Only confirmed cancellation changes projected future obligations.
- **Search:** the four existing deterministic templates. Unsupported questions return a readable error; there is no Gemini interpretation.
- **Goal Planner:** real persisted goals, backend progress/contribution arithmetic, manual proposals, approval/rejection/snooze, versions, decision history, cashflow and shock information. AI disconnection is explicit.

The old Payments/More marketing and account-setup controls remain visual mock placeholders. They perform no real payments, bank-account operations or external merchant actions.

## Customer/session and mutation contract

`POST /api/session` accepts a listed `customer_id` and `demo_acknowledged: true`. This is deliberately mock selection, not password authentication. Subsequent endpoints derive identity from an opaque HttpOnly, SameSite=Strict cookie; they do not accept customer IDs to choose a read/write target. Sessions expire after 12 hours and are lost on backend restart. Persistent actual-customer data remains in SQLite after logging in again.

`GET /api/overview` returns a coherent snapshot: `scope_version`, `revision`, `state_version`, scenario, as-of date, UI account/cashflow data, financial views, recent transactions, goals/proposals/history, subscriptions and category vocabulary. Monetary values are AUD. Monthly contribution arithmetic assumes contributions in the months after the snapshot month through the deadline month; it is not a goal strategy.

Every state-changing resource request must send:

```http
X-Context-Scope: <scope_version from the latest overview>
Content-Type: application/json
```

Include `expected_revision` for optimistic store updates. Applying an action also requires the JSON boolean `confirmed: true`. Creating a pending manual proposal does not apply it and therefore has no confirmation field. Scenario changes require the scope header but no store revision. Scope changes whenever a customer/session or scenario changes, preventing delayed requests from editing another context even if revisions happen to match.

The adapter rebuilds state through `BankingService`, preserving goal version, financial state, allocation and independent-validation checks. The frontend cannot supply a financial snapshot, an account balance, a validated flag, a provider or a validator. Request models reject extra fields. The UI refreshes from returned snapshots after successful mutations, and refreshes on conflicts without pretending the rejected change succeeded.

Mutation origins are checked against the frontend origin (localhost/127.0.0.1 port 5173) or the API's own origin. Override the comma-separated list with `BANKING_ORIGINS` for a deliberate alternate local setup. No permissive cross-origin credential policy is installed. This remains local mock authorization, not a production security design.

## REST endpoints

| Method | Path | Request / result |
|---|---|---|
| GET | `/api/health` | Local/synthetic service status |
| GET | `/api/customers` | Public fictional IDs, first names and personas; no credentials |
| POST | `/api/session` | `{customer_id, demo_acknowledged:true}`; sets mock cookie |
| DELETE | `/api/session` | Signs out and removes the cookie |
| GET | `/api/overview` | Complete selected-context UI snapshot |
| POST | `/api/scenario` | `{scenario, reset?:false}`; returns new overview |
| GET | `/api/transactions?limit=100` | Category, classification source/confidence, amount/date and view-needed narrative |
| PATCH | `/api/transactions/{transaction_id}/category` | `{category, essential, confirmed, expected_revision}` |
| GET | `/api/budgets` | Budget resource in `{data, revision, scope_version}` |
| PUT | `/api/budgets` | `{category, monthly_limit, protected, confirmed, expected_revision}` |
| GET | `/api/bills` | Reconciled calendar and horizon summaries in resource envelope |
| GET | `/api/opportunities` | Existing evidence-based Savings Finder output |
| GET | `/api/insights` | Existing personalised insights after feedback |
| POST | `/api/feedback` | `{action, insight_id?, category?, confirmed, expected_revision}` |
| GET | `/api/subscriptions` | Detected subscriptions and simulated statuses |
| POST | `/api/subscriptions/{subscription_id}/action` | `{action, confirmed, expected_revision}` |
| POST | `/api/search` | `{question}`; read-only dated results |
| GET | `/api/goals` | Goals with backend progress and contribution arithmetic |
| POST | `/api/goals` | `{kind, target, allocated, deadline, confirmed, expected_revision}` |
| POST | `/api/goals/{goal_id}/proposals` | `{changes:{target?,allocated?,deadline?}, source:'user', expected_revision}` |
| POST | `/api/proposals/{proposal_id}/decision` | `{decision:'approve'|'reject'|'snooze', confirmed, expected_revision}` |
| GET | `/api/shocks` | Normalized shock context, not a diagnosis of hardship |
| GET | `/api/planning-context` | Existing privacy-allowlisted schema 1.1, including goal versions |

Feedback actions are `useful`, `not_useful`, `dismiss`, `protect_category`, and `reject_adjustment`. Subscription actions are `request_cancellation`, `confirm_cancelled`, and `confirm_failed`. Goal kinds are `holiday`, `emergency_fund`, `purchase`, and `other`.

Successful mutations return a refreshed overview. Input/service failures return an `error` object with `code` and `message`: 400 for rejected business input/validation requirements, 401 for missing/expired session, 403 for a disallowed mutation origin, 409 for stale scope/store/financial state, 422 for invalid request fields, and 503 for busy storage. Validation errors omit input values to avoid echoing credentials or unnecessary records.

## Scenario workflow and isolation

The global **Demo Scenario Selector** has four options:

1. Selected customer's records (one of the five existing personas).
2. Normal holiday demo.
3. Emergency vet bill paid.
4. Emergency vet bill unpaid.

The latter three use the shared synthetic ledger at September 30, 2026, with a separate, clearly labelled sandbox identity. They do not relabel those finances as the selected real mock persona. A seeded demo goal has a $6,000 target, $1,200 allocated, May 31, 2027 deadline and eight arithmetic contribution periods ($600 each).

| Scenario | Everyday balance | Comfortable/month | Projected cash after known pending |
|---|---:|---:|---:|
| Normal | $2,000 | $900 | $2,000 |
| Vet paid | $150 | $490.73 | $150 |
| Vet unpaid | $2,000 | $900 | $150 |

Switching the three scenarios preserves that session's sandbox proposals/preferences so stale-state protection can be demonstrated. A proposal created normally cannot be approved after the emergency changes the financial state. The unpaid bill remains a single confirmed one-off future/outstanding obligation; the posted bill is already included in balances and is not added again.

**Reset demo to normal** starts a fresh sandbox, restores the normal source ledger and seeded goal, and clears visible sandbox edits without deleting or changing persistent selected-customer data. Selecting customer records returns to the actual persona's persisted budgets/corrections/goals. Sandbox keys are independent random IDs; old sandbox snapshots remain local and unreferenced after reset. There is no destructive database reset endpoint.

## Goal and AI boundaries

Only manual `source:'user'` changes are offered by the UI. Review creates a pending proposal, then a separate explicit decision can apply it. Rejected/snoozed proposals do not modify goals. Approved changes increment versions; `DemoStore.history()` and `BankingService.goal_history()` produce a customer-scoped goal audit list from existing dated snapshots.

Future Gemini integration belongs at `BankingService.planning_context(customer)` (also exposed as `GET /api/planning-context`). Use the existing deterministic `planning/calculation_tools.py` functions for numerical tools. Raw/UI banking snapshots and transaction narratives must not be appended to the model input.

Future independent checker integration is the `proposal_validator` constructor callback of `BankingService`. The adapter's `bind()` currently constructs the service **without a validator**, so `source:'planner'` submissions fail closed. Do not install an allow-all callback to make UI actions succeed. A future host-controlled wiring must pass the real checker, map the selected model goal reference to trusted goal state, and retain approval-time revalidation. The current API also rejects client `validated:true` and fabricated state fields.

No existing savings balance is silently available for newly created goals. Actual personas default to zero explicit designation, so positive allocations require a later trusted designation interface. The isolated demonstration supplies the already-defined $1,200 goal pool and protected reserve explicitly.

## Validation

- Existing Python financial suites and new API tests cover scopes, selected-customer isolation, budgets, corrections, persisted feedback, bill timing, opportunities, subscription states, manual goal review/history, rejected/stale proposals, validator blocking and all three scenario values.
- `frontend/tests/client.test.js` verifies same-origin credentials, mutation scope headers/payloads, rejection errors and unavailable-service messages.
- `npm run build` validates production compilation; native dependencies were supplied as described above without changing tracked `node_modules`.
- Browser smoke checks exercised customer selection, Goal Planner navigation, normal → paid-vet → unpaid-vet values, protected budget saving, pending manual proposal, explicit approval/version update and reset back to the original normal goal. Screenshots and test logs accompany the handoff.

Cold causal replay remains slow for unseen histories; unchanged histories benefit from the existing cache. No bank-scale or multi-process deployment is claimed. SQLite and mock sessions support the local hackathon demo. Browser session storage is not production authentication, and the existing financial uncertainty/forecast limitations remain as documented in `banking-phase2.md`.
