# Adaptive financial events — prototype contract

## Boundary and reuse

Transactions feed deterministic detection, banking insights, and privacy-minimised planning
context. Events explain changes and uncertainty. They do not adjust goals, recommend cuts,
confirm hardship, call Gemini or implement the independent Safety Checker. Existing mutation
confirmation, revision checks and goal approval protections remain intact.

`finstats/shocks.py` and `finstats/savings.py` are unchanged. The six public legacy detector
functions retain their signatures and fixed-default behaviour. The adaptive orchestration is
`normalized_shocks(txns, *, as_of, accounts=(), pending_expenses=(), planned_expenses=(),
event_evidence=(), event_states=None, known_holidays=(), facts=None, calendar=None)`.
`banking_insights` supplies existing financial facts and the reconciled Bills Calendar.
`build_planning_context` carries the same evidence through an explicit privacy allowlist.

## Personal baselines and meaning

The original robust large-spend/new-merchant methods supply candidates. Only earlier dates
supply personal floors and recurrence evidence. Per account, the large-spend floor is the
maximum of $10, half the earlier category median (other expenses as fallback), and 1% of
average completed-month income. The new-merchant floor uses six times the earlier usual
nonrecurring expense median and 3% of income instead. The $10 floor is a noise guard,
not an emergency definition. Original robust-z thresholds remain; new-merchant ratios use the actual personal floor,
not the permissive candidate-generation floor. Scores are not probabilities.
An additional limited-history candidate requires at least three observations and five times
the typical amount. Existing new-merchant warmup remains 60 days. With fewer than three
expense observations, an eligible new-merchant candidate is a limited-history review, not a
claimed statistical anomaly. No prior evidence means no confident personal outlier claim.

Known recurring charges are not automatically new anomalies. A changed recurring amount
can still be detected. Burst, category-spike and bill-change evidence uses the existing methods;
liquidity episodes use the legacy $200 buffer with recovery above $400 to avoid alert churn.

Four separate fields matter:

- `statistical_anomaly`: evidence of changed behaviour, independently of current wealth.
- `potential_disruption`: low/moderate/high/unknown, an explainable contextual band.
- `expectedness`: expected/unexpected/unknown, with a separate source.
- `confidence`: limited/moderate/higher, strength of detection evidence, not probability.

Current balance, explicit protected reserves, known upcoming bills, completed-month surplus,
comfortable/stretch capacity and income variability accompany each event. Known obligations
are applied to their funding account; an unassigned explicit obligation uses the primary
account once. Projected unreserved cash below $200 or amount/resources above 50% is high;
above 20% is moderate. Income CV above 0.3 combined with an event exceeding average surplus
also raises moderate concern. Posted purchase ratios restore that purchase to the denominator
for comparison with pre-event resources; they never debit it again. These heuristic bands
need real-data calibration. They describe current context, not proof an old event caused the
current shortage, confirmed hardship, or a proposed goal strategy. No balance means unknown.

## Income interruption and persistent changes

`detect_income_interruptions` groups positive income by account and normalised source,
excluding ordinary interest. Four initial credits must establish weekly (6–8 days), fortnightly
(12–16), or monthly (27–32) timing, all initial gaps within three days of their median.
Initial amount MAD/median must be at most 0.15 and CV at most 0.3. Uneven freelance invoices
therefore have no assumed payday. Separate sources are monitored independently.

Monthly schedules use calendar months, retain end-of-month anchors where evident, and clamp
short months. Weekends and caller-supplied holidays move the expected window to business days;
two further business days form the grace period. Missing income is reported only after grace.
No nationwide holiday calendar is silently assumed. A later deposit within the same cycle
changes the same interruption ID to delayed. Consecutive absences grow one episode. A credit
below baseline minus max(20% baseline, three MADs) is reduced income. The baseline uses up to
six prior unreduced payments. Three reduced or missing cycles constitute a sustained pattern.
Income shortfalls are not expense debits and missing income is never an invented credit.

`detect_persistent_changes` adds:

- Category spending: eight preceding complete weeks, with at least two consecutive weeks
  above median + max(30% median, three MADs); four elevated weeks is sustained.
- Gradual bill increases: at least six charges; recent-three median above first-three by
  10%, monotonic steps no larger than 10% each.
- Completed-month income: at least six months, recent-three CV increasing by over 0.15,
  or three months consistently below 80% of earlier median.
- Repeated low liquidity is one episode until recovery, with sustained pressure distinguished.

Episode IDs anchor the source/category and first affected period. Overlapping weekly category
alerts and ordinary contributing purchase alerts are absorbed into the sustained episode,
preserving methods and scores. Security bursts and separate purchases above three times
the changed pattern’s median purchase size retain independent review events. Different analytical layers may still describe
the same cashflow: event amounts are explicitly non-additive.

## Expectedness and payment timing

Expectedness requires evidence: an explicit plan linked to the transaction and known before
it, a linked confirmed bill, earlier recurring behaviour, or user/scenario confirmation.
Only explicit evidence/confirmation says unexpected. An unusual merchant or category alone
leaves expectedness unknown. `PlannedExpense` is a validated service-level input, not a new
standalone planning workflow. Source `recurring` flags are accepted as input evidence.

Posted purchases are already in balances and completed-month history. Outstanding confirmed
obligations enter the reconciled projection once; `already_in_projection` makes that explicit.
Anticipated expenses remain uncertain and are not deducted as confirmed payments. Recurring
calendar entries remain estimates unless confirmed by explicit obligations. Transfers never
become new spending. Event totals must not be summed into a second ledger deduction.

The existing shared veterinary ledger remains:

| Scenario | Everyday cash | Comfortable/month | Cash after known pending |
|---|---:|---:|---:|
| Normal | $2,000 | $900 | $2,000 |
| Vet already paid | $150 | $490.73 | $150 |
| Vet unpaid | $2,000 | $900 | $150 |

## Identity, lifecycle and persistence

Singleton identity hashes account + source transaction ID, independently of analysis date,
category or detector combination. Distinct same-day/amount purchases stay distinct. Broad
patterns have separate source/period identities. Methods and statistical scores are retained
when several detectors identify the same transaction. IDs require stable, nonempty source
transaction IDs; duplicates are rejected. New deterministic IDs replace v1.1 method-dependent
IDs. Exact old individual-event personalised-insight feedback is recognised by aliases; merged
or merely overlapping old event feedback is deliberately not propagated to new risks.

SQLite snapshot `shock_events` stores customer/scenario-scoped status, explicit expectedness,
reviewed evidence version, action history and optional comparable-payment memory. Old snapshots
without this field still load. No unrelated database or service is introduced.

Actions are acknowledge, confirm_expected, confirm_unexpected, dismiss, resolve and reopen.
Acknowledgement records review status and does not turn inferred expectedness into user
confirmation. Reviewed events remain inspectable, but their personalised notifications stop. Explicit reopen
or changed material evidence (amount, underlying transactions, type, stage, missed cycles or
impact band) requires another review. Small score/method changes alone do not recreate alerts.
Independent critical cashflow insights remain visible after event dismissal.

Opt-in expected-payment memory requires the same account/merchant/category and an amount
within 20%, plus recurring-bill evidence or a later annual occurrence (at least 300 days).
It never suppresses an entire category; high current disruption still requires review.
Feedback known after a replay date is excluded. Historical source actions remain in the
history, while current status is the latest confirmed interpretation.

## API and React

Existing GET `/api/shocks`, `/api/overview`, `/api/insights` and `/api/planning-context` expose
richer events; overview also returns `shock_history`. Stable internal IDs are used for UI
mutations. Raw source transaction IDs and private underscore fields are removed from REST
event cards. Same-origin/session, selected scenario, explicit confirmation and optimistic
revision protections still apply.

POST `/api/shocks/{event_ref}/action` accepts:

```json
{
  "action": "confirm_expected",
  "evidence_version": "copy the current event evidence_version",
  "remember_expected": false,
  "confirmed": true,
  "expected_revision": 0
}
```

Send the existing session cookie, same-origin request and `X-Context-Scope` header from
the current overview, and use the actual current revision. Stale revision/evidence gives 409; foreign or missing events
are rejected. `remember_expected` requires a boolean. Actions return refreshed data through the
existing mutation flow. Goal mutation and proposal validation endpoints are unchanged.

Insights and Goal Planner reuse the same `ShockEvents` component. It provides concise copy,
separate confidence/expectedness/impact, optional evidence, feedback controls, reviewed events
and history. A category-review link opens existing Accounts correction tools. Source data and
goals are never changed by an event acknowledgement. No new frontend dependency is required.

## Model boundary — schema 1.2

Planning context changes from 1.1 to 1.2; consumers must accept the new version. Existing
financial fields remain, while events add evidence, expectedness, lifecycle, income and
pattern semantics. Event refs become stable pseudonymous hashes rather than position-based
aliases. Detection uses date-only `detected_at` with `detection_precision: day`, matching the
source's precision rather than inventing a wall-clock timestamp.

Only selected numeric/date/statistical evidence and fixed explanations cross the allowlist.
No raw transaction histories, account/customer/source transaction IDs, merchant narratives,
comparison keys or internal aliases go to Gemini. The future planner can distinguish affordable
planned purchases, confirmed emergencies, outstanding obligations, delayed salary and sustained
changes. `questions` and `uncertainty` identify what still needs clarification. The context does
not tell Gemini what goal adjustment to choose. No Gemini network call exists in this upgrade.

## Reproduction and evaluation

From backend, using the repository virtualenv:

```bash
python -m unittest discover -s tests -v
python -m planning.demo examples
python -m planning.banking_demo examples
python -m evaluation.run_shocks
```

From frontend: `npm test` and `npm run build`. This checkout's installed native dependencies
were built for Windows; the verification run uses existing Darwin tooling via NODE_PATH and
ESBUILD_BINARY_PATH without changing dependencies or lockfiles.

`evaluation/shock_scenarios.py` supplies 14 reconciled synthetic ledgers, target detection
labels and supporting reasons. The report includes target-level TP/TN/FP/FN, expectedness,
confidence, impact, contributed methods and incidental event counts. Results: 10 TP, 4 TN,
0 FP, 0 FN, precision 1.0 and recall 1.0 **on authored fixture targets only**. Unlabelled
incidental events are not claimed as true negatives or included in accuracy. Planned but
unusual activity is a positive review target even when expected. The metric is not surprise
classification, fraud detection, emergency prediction or real-world performance.

Additional regression tests cover deduplication, changing conditions, lifecycle persistence,
customer isolation, stale evidence, privacy, annual memory, all existing personas and the
normal/paid/unpaid/anticipated veterinary scenarios. Examples include both internal event
records and privacy-minimised veterinary/missing-salary planning contexts under
`examples/shock-evaluation/`; `examples/vet_paid.json` uses the canonical demo ledger.

## Limits and assumptions

This is deterministic synthetic-data prototype behaviour. Thresholds are explainable heuristics,
not calibrated risk probabilities. Four initial stable credits can misidentify later irregular
income; changing employers, split deposits, very long delays, missing histories and annual
income need stronger source metadata. Holidays require explicit caller input. Several missed
cycles cannot prove permanent income loss; deposits outside their cycle may remain ambiguous.
Completed weeks/months delay pattern detection. User source flags/plans and explicit scenario
annotations are evidence inputs, not externally verified facts. Contextual bands can change
with balances and obligations without proving causality. SQLite snapshot history is local demo
storage, not production authentication/audit infrastructure. No real-bank validation was done.
