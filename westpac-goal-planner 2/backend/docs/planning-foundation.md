# Financial insights and planning-context foundation

The public flow is transactions → banking facts/estimates → normalized events → explicitly allowlisted planning context. Gemini will propose strategies later. The independent checker will validate proposed actions later. This change implements neither component, makes no external API calls, adds no packages, and leaves React unchanged.

This simulates the category, cashflow and savings-opportunity intelligence described by [Westpac's Insights Hub](https://www.westpac.com.au/personal-banking/online-banking/insights-hub/). It is synthetic demonstration code, not a bank integration.

## Run and inspect

From `westpac-goal-planner 2/backend` using Python 3.10+:

```bash
python -m unittest discover tests -v
python -m planning.demo examples
```

The demo writes `examples/before_event.json`, `normal.json`, `vet_paid.json`, `vet_unpaid.json`, and `vet_anticipated.json`. No credentials are required. The committed-source generator is deterministic and does not modify the original five-persona fixtures.

```python
from datetime import date
from finstats import load_transactions, financial_insights, normalized_shocks
from planning.context import build_planning_context, context_json
from planning.schemas import AccountSnapshot, Goal, Preferences

transactions = load_transactions('data/mock_transactions.json', user_id='u003')
as_of = date(2026, 11, 15)
insights = financial_insights(transactions, as_of=as_of)
shocks = normalized_shocks(transactions, as_of=as_of)
context = build_planning_context(
    transactions, as_of=as_of,
    goals=(Goal('internal-goal-id', 'holiday', 6000, 1200, date(2027, 7, 15)),),
    preferences=Preferences(protected_categories=('Health',)),
)
print(context_json(context))
```

Without explicit account designations, the example warns that the goal's claimed allocation is not backed by designated savings. Supply `AccountSnapshot` objects to resolve that uncertainty. Do not convert present profile balances into historical snapshots. A dated opening balance can be used with later `balance_after` records; dated loan balances require separate snapshots.

## Interfaces and provenance

- `finstats.financial_insights(txns, *, as_of, accounts=(), protected_categories=())` returns a JSON-compatible dictionary. This internal object includes account IDs and normalized merchant keys for reconciliation. **Never send it directly to Gemini.**
- `finstats.bill_insights(txns, as_of, horizon_days=30)` infers recurrence per account, preserving the existing detector algorithm. `source_flagged` means source metadata says recurring; `inferred` means statistical inference. Neither means a confirmed contract. At least three occurrences are needed. No next date is returned within the horizon for a lapsed series.
- `finstats.normalized_shocks(txns, *, as_of)` returns causal, deduplicated event dictionaries. Nonempty unique transaction IDs and a single customer's data are required by the combined context API.
- `planning.context.build_planning_context(...)` accepts typed goals/preferences, dated account snapshots, pending expenses and optional posted-event evidence. It returns the only model-bound payload.
- `planning.context.context_json(context)` serializes with `allow_nan=False`.
- `planning.calculation_tools.required_contribution`, `contribution_projection`, and `contribution_cashflow` provide read-only arithmetic for caller-supplied proposals. They do not select a strategy, promise affordability, or approve recommendations. `months` is an explicit count of planned contribution periods, not an automatically guessed calendar convention. Required contributions round upward to cents.

Derived facts: signed monthly flows, category totals, observed balance snapshots, discretionary/essential splits, and posted expense totals. Transfers are excluded from income/expense calculations.

Estimates: recurring dates/amounts, regular income, statistical variability, confidence labels, surplus trends, savings capacity, recurring-bill runway, and potential discretionary review amounts. Confidence is a history/variability heuristic, not a probability. Review opportunities are observed spending evidence, not guaranteed reductions. Essential or protected categories are never automatically marked reducible.

Explicit synthetic/user inputs: goal type, target, allocation, deadline, preference switches, protected categories, protected reserves, designated goal savings, pending obligations and confirmation that an event was unexpected. The demo labels the veterinary event with `PostedEventEvidence`; the detector does not infer medical necessity from a merchant string.

## Financial-state semantics

`schema_version` is the contract version. `as_of` is the inclusive date cutoff. `state_version` is SHA-256 of canonical allowlisted context before adding the hash. It changes when financial facts, goals or preferences change. It is not authentication. The future checker should require the version corresponding to the state it verifies.

Monthly capacity reuses `savings_capacity()` unchanged. Partial boundary months are excluded based on transaction dates. Current-month flows are reported separately; an incomplete month can affect cash immediately without yet affecting historical capacity. Statement coverage is not available, so a month ending with its last transaction before the last calendar day can be conservatively excluded even when the statement covers that day.

A snapshot is usable only if its date is on/before `as_of`. A later observed transaction balance supersedes the snapshot balance. Reservations/designations remain explicit snapshot inputs. Savings default to **zero designated goal availability**. `goal_available` is a designated pool including existing goal allocations, not extra free money. `protected + goal_available` must fit the snapshot; later shortfalls are reported. No account-name-based reserve inference is attempted.

Runway is the primary spending account's balance minus estimated recurring obligations before its next detected payday, or 14 days. Same-day payday precedes bills, following the existing function's assumption. It excludes ordinary future spending and unconfirmed income. It is not a complete affordability or hardship assessment.

### Shock timing and replay

Every historical detector evaluation sees only its day's transaction prefix. Recurring series and bill amounts are re-estimated from that prefix, scoped by account for runway. Legacy detector functions remain available unchanged; use the new normalization API for causal replay. Category alerts mature within a week, with `observed_on` recording evaluation timing.

Overlapping detector alerts form connected components through shared transaction IDs. Event amounts sum the unique posted debits once, rather than adding detector amounts. Thus a burst/category cluster may represent several linked transactions. Runway alerts have no posted amount and remain separate projections.

`severity=review` means an anomaly warrants review. `severity=cashflow_risk` means a recurring-bill projection breaches a buffer. Neither confirms hardship. Evidence retains each detector's score; infinite scores become JSON null with `score_is_unbounded=true`.

Posted expense: already included in balance and relevant historical flows, with `additional_future_debit=0`. Per-event capacity attribution is a counterfactual on the same complete-month window excluding that event; it is numerical evidence, not a causal guarantee or strategy.

Unpaid expense: a known additional obligation, supplied as `PendingExpense(status='unpaid')`. It reduces the displayed near-term projection if due within its window, including overdue items, without modifying the posted ledger or historical capacity.

Anticipated expense: `status='anticipated'`; remains separately visible and does not silently become a certain debit.

Link a pending item to its eventual `transaction_id` when it posts. It is then suppressed to prevent a second charge. Duplicate pending event IDs are rejected. The caller must reconcile pending obligations against inferred recurring bills and preserve that linkage across imports. Do not submit the same recurring bill as an additional pending obligation. Cross-account funding and unlinked duplicate bank imports are not inferred.

### Privacy boundary

All model fields are constructed explicitly, including nested structures. No profile serialization, customer names, addresses, credentials, account/card/transaction IDs, merchant narratives, free-text goals, or free-text preferences cross the boundary. Category names map to a closed vocabulary (`Other` for unknown labels); goal kinds, event kinds and preference switches are constrained. References become local `goal-1`/`event-1` values. Keep the internal ID mapping in trusted application state for future approval handling.

Do not append raw customer objects or narrative text to the resulting payload. Treat output as sensitive financial summaries even though this project uses synthetic records. The foundation does not include a runtime synthetic-data attestation or production authorization system.

## Why existing customers calculated zero capacity

The $560 frontend value is a static `demoGoal.safeCapacity`, unrelated to the engine. A full-history investigation reproduced:

| User | Mean income | Essential | Discretionary | Mean net | Centre | Volatility | Comfortable | Stretch |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| u001 | 2626.13 | 1644.85 | 611.73 | 369.55 | 179.76 | 356.76 | 0 | 0 |
| u002 | 23540.12 | 18727.84 | 4596.78 | 215.50 | -1092.08 | 1805.59 | 0 | 0 |
| u003 | 6823.90 | 5410.60 | 1235.56 | 177.75 | 106.56 | 531.89 | 0 | 0 |
| u004 | 3163.26 | 2786.97 | 291.83 | 84.46 | 2.69 | 417.09 | 0 | 0 |
| u005 | 10384.63 | 7718.68 | 1644.92 | 1021.02 | 931.59 | 1020.51 | 0 | 240.06 |

The synthetic spending leaves small surpluses for most personas; quarterly bills, irregular income and planted shocks cause volatility. The engine subtracts that volatility from its conservative centre, flooring capacity at zero. This is not simply a transfer bug: savings transfers are already excluded. Both partial November months are dropped, leaving 11 complete months. The generator also raises opening balances to prevent negative histories, so a positive ending balance does not prove sustainable cashflow. Loan balances are static fixture metadata.

JSON carries explicit per-transaction essential flags; CSV infers them by category and can produce different discretionary/stretch totals. JSON remains the canonical input for this interface. No existing thresholds, source transactions, fixture generation or five-persona diversity were rewritten to force a convenient answer.

## Holiday/vet demonstration

The additive `planning.demo` ledger contains nine months of $4,000 income, $2,600 essential spending, $500 discretionary spending and $900 internal savings transfers. Opening balances are $2,000 everyday and $1,200 savings. Every closing balance reconciles to those transactions. By September 30 savings are $9,300: $8,100 explicitly protected for a separate synthetic obligation, $1,200 designated and allocated to the holiday.

The $6,000 holiday target has $1,200 allocated and a May 31, 2027 deadline: eight proposed month-end contribution periods from October through May would require $600 each. This arithmetic is demonstrated by a tool test, not prescribed in the model context.

`before_event` is the true September 29 historical view. `normal` is a September 30 counterfactual without the vet expense, allowing a same-date comparison. `vet_paid` shares every normal transaction/ID and appends one $1,850 essential Pets debit on September 30. Explicit scenario evidence confirms the emergency veterinary nature. The event's new-merchant detector supplies anomaly evidence; the resulting low balance supplies separate cashflow-risk evidence.

| Same-date scenario | Everyday balance | Comfortable/month | Near-term projection after known pending |
|---|---:|---:|---:|
| Normal | $2,000 | $900 | $2,000 |
| Vet paid | $150 | $490.73 | $150 |
| Vet unpaid | $2,000 | $900 | $150 |
| Vet anticipated | $2,000 | $900 | $2,000 |

The paid event falls on a complete month's last day, so both immediate cash and historical capacity change. A mid-month paid event immediately changes cash but does not enter complete-month capacity until month-end. The paired fixtures isolate one event, rather than inventing unrelated summaries. The deliberately stable base is a transparent demo assumption; it does not replace the more varied existing personas.

## Remaining integration work

The separately developed Gemini planner should consume `build_planning_context` and propose schema-constrained strategies. Its checker must independently retrieve trusted state, verify contributions, reserves and aggregate recommendations, and enforce state freshness. Approval/rejection UI, HTTP transport, persistence, authentication, calibrated income forecasting, timezone-aware intraday posting order and production-grade incremental detector execution remain outside this change. Causal replay reruns existing detectors for date prefixes; appropriate for these fixtures, not optimized for bank-scale streams.

## Subsequent banking extension

The expanded banking interfaces and model schema 1.1 are documented in
[banking-phase2.md](banking-phase2.md). That extension adds a bill calendar with explicit
replacement links, user budgets, richer opportunity/insight evidence, persisted feedback,
category corrections and goal review. Use `banking_insights()` for the unified internal/UI
object and `BankingService.planning_context()` for persisted application state. The original
`financial_insights()` and savings/detector APIs remain compatible.
