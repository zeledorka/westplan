# Banking intelligence and interactive-state integration

This phase extends the existing 43-test foundation without replacing the savings engine, six original detector implementations, original customer fixtures or React screens. No Gemini API, independent Safety Checker, real cancellation service, Tax Organiser or HTTP server is implemented.

## Public interfaces

All paths below are relative to `westpac-goal-planner 2/backend`.

| Module | Public interface | Purpose |
|---|---|---|
| `finstats/banking.py` | `banking_insights(...)` | Unified frontend/internal banking object, including original facts plus budgets, bill calendar, savings finder, shocks and personalised insights |
| `finstats/budgets.py` | `BudgetLimit`, `budget_summary(...)` | Calendar-month actuals against explicit limits and provenance |
| `finstats/bills.py` | `bills_calendar(...)`, `expected_payment(...)` | Paid/latest recurring records, forecasts, one-off obligations and 7/14/30-day summaries |
| `finstats/opportunities.py` | `find_savings_opportunities(...)` | Evidence and conditional savings ranges, with protected/essential exclusions |
| `finstats/personalised_insights.py` | `personalised_insights(...)` | Deterministic observations, stable IDs and risk-preserving feedback handling |
| `finstats/categorisation.py` | `Classification`, `classify_transaction(...)`, `classified_transactions(...)` | Merchant rules, explicit-category preservation, manual overlays and uncertainty |
| `finstats/search.py` | `TransactionQuery`, `parse_question(...)`, `search_transactions(...)` | Four read-only deterministic question templates |
| `finstats/contracts.py` | `CATEGORIES`, `category(...)`, `money(...)` | Shared vocabulary and numeric validation; previous schema imports remain compatible |
| `planning/context.py` | `build_planning_context(...)` | Schema 1.1 model-bound allowlist and content version |
| `planning/store.py` | `DemoStore`, `StaleState` | SQLite per-customer snapshots, historical reads, atomic optimistic updates |
| `planning/service.py` | `BankingService` | Trusted financial-provider facade, persistence and explicit approval operations |
| `planning/banking_demo.py` | `write_examples(...)` | Existing-persona internal and model-bound examples |

The original `financial_insights()` remains compatible. Use `banking_insights()` for the expanded banking object. **Internal banking output includes merchant/account/transaction references and must not be sent directly to Gemini.**

## Running examples and tests

From `backend`, using Python 3.10+ and the existing environment:

```bash
python -m unittest discover tests -v
python -m planning.demo examples
python -m planning.banking_demo examples
```

`customer_u003_insights.json` is an internal/UI object based on David's existing synthetic transactions. `customer_u003_planning_context.json` is the explicit model-bound projection. Its $250 dining limit is labelled `demo_default`, not an asserted customer budget. Its holiday allocation is zero; no current savings are silently designated for that example.

The five existing demo-context filenames remain reproducible. Shared-ledger normal/paid/unpaid results are unchanged:

| September 30, 2026 | Everyday | Comfortable/month | Near-term after known pending |
|---|---:|---:|---:|
| Normal | $2,000 | $900 | $2,000 |
| $1,850 veterinary bill posted | $150 | $490.73 | $150 |
| Veterinary bill unpaid | $2,000 | $900 | $150 |
| Veterinary expense anticipated | $2,000 | $900 | $2,000 |

## Budgets

`BudgetLimit(category, monthly_limit, protected=False, source='user_defined')` validates finite nonnegative money, expense categories and provenance. Sources are `user_defined`, `inferred`, or `demo_default`; absent limits are `unset` with null limit/remaining. No budgets are invented by default.

`budget_summary` uses only posted expenses in the selected calendar month through `as_of`. Refunds offset spending. Transfers are excluded. Results include essential/discretionary actuals, signed remaining budget, protection, upcoming obligations and status. Approaching means at least 80% of the limit; over means strictly greater than the limit. Budget remaining is explicitly **not available cash**. Limits persist as repeating monthly limits until explicitly changed; no rollover or envelope funding is assumed.

`BankingService.set_budget(..., confirmed=True, expected_revision=...)` accepts user-defined limits only. Future AI goal proposals cannot modify budgets through their goal-change schema. Protected budgets also become protected categories in model context and opportunity filtering.

## Bills and subscriptions

Recurring forecasts use account-scoped series observed on/before `as_of`. Source recurring flags are evidence, not confirmation of contracts or dates. A detected latest amount step uses its observed new amount for the next estimate; otherwise the recent median is retained. Records distinguish observed posting dates, estimated recurring dates and explicitly confirmed one-off due dates.

Calendar `items` includes each series' latest paid occurrence plus upcoming occurrences. `summary_days` separates confirmed one-off amounts, recurring estimates, their combined expected total and uncertain anticipated amounts. Overdue unpaid obligations are included. Internal transfers never appear as bills.

One-off obligations reuse `PendingExpense`. Link its `transaction_id` when it posts, suppressing a duplicate future charge. To replace an inferred bill with a confirmed obligation, set `replaces_bill_id` to the exact upcoming internal occurrence ID; this removes that estimate before adding the confirmed amount. Multiple replacements of one occurrence, duplicate event IDs and unknown replacement IDs are rejected. Unlinked semantically duplicate bills still require user reconciliation; merchant/amount fuzziness is not a sufficient identity guarantee.

The subscription workflow is simulated and limited to inferred, nonessential `Subscriptions` series:

```text
active → cancellation_requested → cancelled
                              └→ cancellation_failed → cancellation_requested
```

Every transition requires explicit confirmation. A request or failure leaves future forecasts unchanged. Only a confirmed simulated cancellation suppresses forecasts and corresponding recurring opportunities. History, balances and historical savings capacity never change, and no refund is assumed. Cancellation is dated and scoped to the customer; replaying an earlier store snapshot does not see it. Direct analytics callers supplying state dictionaries must likewise supply the as-of store snapshot.

## Savings Finder and personalised insights

The finder retains the earlier generic review evidence and adds recurring discretionary costs, detected increases, consistently observed discretionary categories, material historical variation and possible overlapping subscriptions. Multiple services do not prove duplication: overlap is explicitly unconfirmed.

Ranges always start at zero. Upper bounds use observed recurring cost, observed price difference, or average spending minus that customer's lowest complete-month spending. Recurring cost conversion is `amount × 365.25 / 12 / inferred_period_days`, so a 31-day estimate differs from a 30-day estimate. A stable category can have a zero quantified reduction range while still providing review evidence. Ranges are conditional, may overlap and must not be summed or treated as guaranteed savings. No goal strategy is selected here.

Personalised observations use the selected customer's statistics, budget and normalized events. IDs are stable per underlying event or category/period. A normalized event becomes one event insight; historical runway alerts are replaced with a single current cash-risk observation. Separate expense and cash-risk observations convey different facts. Category review/increase alerts are suppressed when the event already explains that category.

Cash-buffer risk and unusual purchase-burst security reviews are critical and remain visible despite dismissal/dislike. These are review signals, not confirmed hardship or fraud. Other insights can be hidden by dismiss/not-useful feedback. The latest useful/not-useful/dismiss action controls visibility, allowing a user to restore an informational insight.

## Persistence, preferences and approvals

`DemoStore(path)` uses a local SQLite file with append-only customer snapshots. Every write is atomic, requires the expected revision and has an effective date. Historical reads select the latest snapshot on/before that date. Backdated writes are rejected. The store preserves feedback/decision history and uses no network or paid service.

The host constructs `BankingService(store, provider, proposal_validator=None)`. The trusted `provider(customer)` must load that customer's source data and return:

```python
{
    'txns': transactions,
    'as_of': selected_date,
    # optional: accounts, pending_expenses, event_evidence
}
```

Customer IDs and provider callbacks are host-owned, not request financial payloads. The service rejects another customer's labelled transactions. `banking_view(customer)` returns internal/UI insights, store revision, goals, proposals and subscription states. `planning_context(customer)` returns the allowlisted context, including goal versions. Read-only analytics never update transaction history or store state.

Mutation methods:

```python
service.set_budget(customer, BudgetLimit('Eating out', 250), confirmed=True, expected_revision=revision)
service.correct_category(customer, transaction_id, 'Shopping', False, confirmed=True, expected_revision=revision)
service.record_feedback(customer, action='protect_category', category='Health', confirmed=True, expected_revision=revision)
service.create_goal(customer, Goal('holiday', 'holiday', 6000, 1200, deadline), confirmed=True, expected_revision=revision)
proposal = service.propose_goal_change(customer, 'holiday', {'target': 6500}, expected_revision=revision)
service.decide_proposal(customer, proposal['proposal_id'], 'approve', confirmed=True, expected_revision=new_revision)
```

Read the latest revision after each successful mutation. Goal changes support target, deadline and allocated savings, with a monotonic goal version and a persisted decision list. Proposals remain pending until explicitly approved; snooze/reject do not modify the goal. Approval rebuilds financial state from the trusted provider and rejects changed financial, budget, preference or goal state. It rechecks allocations against designated savings and rolls back failed approvals. No bank transfer occurs.

For a future Gemini proposal, use `source='planner'`. This fails closed unless the host has supplied an independent `proposal_validator`. The callback receives `{'goal_ref': ..., 'changes': ...}` plus the server-rebuilt planning context and must return exactly `True`; it is invoked again at approval. A caller cannot provide `validated=True` or its own financial version to bypass validation. The included test callback is a test double, not an implemented Safety Checker. A production transport must bind trusted source/identity and validator wiring server-side.

`record_feedback` supports useful, not-useful, dismiss, reject-adjustment and protect-category actions. Insight IDs are checked against that customer's generated insights. Distinct adjustment references are required for rejection counting; repeated retries of the same rejection do not count twice. Two rejected adjustments in a category add an unwanted-reduction preference to the Gemini context. Feedback is not plan approval and cannot configure or disable the checker.

## Categorisation and search

Known merchant token-prefix rules reuse `normalize_merchant`. Existing explicit categories win by default, even when a learned merchant correction exists. An explicit manual transaction correction is the justified override. It is stored separately from immutable banking rows and also seeds corrections for subsequently uncategorised matching merchants. Ambiguous marketplaces and unknown merchants remain `Other`, zero confidence and unknown essentiality; the analytics adapter conservatively excludes them from automatic discretionary reductions.

The rules are deliberately incomplete. No classifier is trained and no AI call occurs. Existing synthetic merchant/category pairs are tested. A later import endpoint should call these helpers and persist imported records through the trusted data provider.

`service.search(customer, question)` supports these exact question families:

- How much did I spend on groceries last month?
- How much did I spend on transport over the last three months?
- Which subscriptions increased in price?
- What were my largest expenses this year?

The groceries/transport templates can use either supported time phrase. "Last three months" means three completed calendar months. Unsupported questions fail explicitly. Structured queries constrain category, dates, kind and result limit. No SQL or arbitrary code is generated or executed.

## Privacy and frontend handoff

Model schema 1.1 adds allowlisted budgets, calendars, opportunity evidence/ranges, insight facts and feedback preferences. Internal IDs and raw merchant narratives are replaced with local references; categories use the closed vocabulary. No customer/account/card identifiers, credentials or free-text feedback are serialized. The bounded shock-result cache holds only existing synthetic records/results in memory and returns defensive copies; it writes no extra files.

The current React application still uses mock data and has no HTTP backend. This phase adds no frontend transport or views. Next integration work is a small customer-bound backend adapter and React views/forms for budgets, bill list, insights/feedback, corrections, goal proposals and simulated cancellations. Display internal UI records through the trusted service, and send only `planning_context` to Gemini. Preserve an internal mapping for model references when interpreting future proposals.

Mock authentication remains a hackathon limitation. Confirmation booleans represent explicit UI actions and are not production identity or consent proof. No new HTTP surface is exposed in this phase. Recurring dates, income confidence and opportunity ranges remain estimates; no full daily-spending cashflow forecast or calibrated categorisation probability is claimed. Initial causal replay remains relatively expensive for a new history; caching accelerates unchanged repeated reads, not bank-scale ingestion. CSV essential flags remain inferred by category, while JSON remains the canonical demo source.
