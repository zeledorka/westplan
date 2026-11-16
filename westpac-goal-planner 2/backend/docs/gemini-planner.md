# Gemini adaptive goal planner

## Architecture and activation boundary

The existing pipeline is retained: banking transactions → financial insights and shock events →
privacy-minimised context v1.2 → one Gemini planner → read-only Python calculations →
`draft_unverified` results. No independent Safety Checker is implemented. There is no route from
these drafts to approved goal changes, payments, transfers, subscriptions or account mutations.
The existing `BankingService.propose_goal_change(source='planner')` guard is unchanged and still
requires an independently configured validator. Draft IDs are absent from the existing approval
proposal collection, so posting a draft ID to its decision endpoint fails.

`planning/gemini.py` adapts the official `google-genai` SDK. `agent.py` owns an explicit bounded
function-calling loop. `agent_tools.py` adapts existing arithmetic; `agent_models.py` validates
untrusted structures. `planner_service.py` binds server-authorised customers, goals and versions;
`planner_store.py` keeps conversations in the existing SQLite database. There is no new service,
second model, ML framework or duplicate bank ledger.

The model creates arbitrary strategies from contribution amounts, monthly/fortnightly frequency,
pauses, targets, deadlines, priorities and explanatory review actions. There is no preset A/B/C
strategy menu or scenario-name branch. Gemini can maintain a plan, ask questions, compare several
options or decline a recommendation. Up to four options may reference computed strategies.
Hypothetical target/deadline fields are typed; unsupported fields, tools, frequencies, foreign
references and non-finite amounts are rejected. Narrative `proposed_actions` are explanatory text,
never executable instructions. This phase intentionally has no executable AI action schema.

## Configuration and official interfaces

Use Python 3.11 and install backend `requirements.txt`. The verified SDK is `google-genai==3.3.0`.
`python-dotenv` loads repository-root `.env` without overriding explicitly exported variables:

```dotenv
GEMINI_API_KEY=your-backend-only-key
GEMINI_MODEL=gemini-3.8-flash
```

The tracked root `.env.example` contains an empty key. `.env` remains ignored. React receives
neither the key nor environment-file contents. Error responses and application logs never include
raw SDK exceptions or request contents. Known credentials, API-key-shaped text, email addresses
and long numeric identifiers in chat are redacted. Free text cannot be perfectly anonymised;
users are explicitly asked not to enter personal identifiers.

The default is `gemini-3.8-flash`, confirmed available by an authenticated `models.get` call.
The model is configurable; no fallback model or fixed recommendation is silently substituted.
The implementation follows the official [Google SDK function-calling and JSON-schema interfaces](https://googleapis.github.io/python-genai/),
including `Client.models.generate_content`, typed Content/Part messages, manual tool responses
and `response_json_schema`. Original model Content, including thought signatures, is preserved
when returning tool results. Automatic SDK function execution is disabled.

## Model reasoning and arithmetic

Each request supplies the existing v1.2 context once, the selected pseudonymous goal reference,
a bounded question, minimised approved goal-version history and at most three compact prior
conversation turns. Old turns are labelled historical when their financial fingerprint differs.
Raw banking histories, merchant narratives, customer names, account numbers and internal goal IDs
are excluded from the model payload. Only the server maps aliases back to current goal records.

The app does not currently record approved contribution schedules or explicit goal priorities.
The model is told these are unknown. A selected goal's type supplies its minimised description;
no invented personal narrative is added. Temporary grocery protection and natural-language
preferences affect this conversation only. Existing category protections remain in context.

The available tools are:

- `calculate_strategy`: arbitrary typed plans for **every** authorised goal exactly once,
  plus explicitly hypothetical additional one-off expense or missing income.
- `finish_planning`: signal readiness for a structured final response; it has no side effect.

The compound calculator reuses `required_contribution`, `contribution_projection` and
`contribution_cashflow`. It returns required periodic amounts, principal-only projections,
estimated completion dates, deadline feasibility, aggregate contributions versus comfortable
and stretch capacity, source projected cash after known obligations, and a one-month comparison.
Monthly schedules use calendar anniversaries; fortnightly schedules use actual 14-day intervals.
A contribution starts one period after the as-of date, following any stated skipped months.
Fortnightly monthly equivalents use 26/12 for comparison, not deadline arithmetic. Amounts use
the existing cent-rounding function. Deadlines are limited to ten years; distant completion
estimates are bounded and may be unavailable. No interest or new income is invented.

The one-month comparison uses the existing pending-adjusted source projection, so it does not
subtract posted transactions or outstanding bills again. Additional shock tool parameters are
labelled hypothetical additions. If several transaction accounts make protected funding reserves
ambiguous, the cash comparison is unavailable and clarification is needed. It does not sum all
account reserves against an unidentified spending account.

Gemini may call several calculations, review the returned figures, and revise strategies. A final
structured call to the **same model** explains selected calculations. It cannot introduce new
computed options without successful calculation refs. Figures displayed by React come from
Python. Model prose is unverified and may still disagree with figures; semantic financial review
is not claimed and is deferred to the independent checker phase.

## Bounds, failures and observability

One assessment allows four **logical** generation stages (up to three tool rounds plus structured
output), eight function calls and a shared 100-second execution deadline, including context preparation
and retry sleeps. Each network attempt receives at most 30 seconds, clipped to the remaining deadline.
By default, at most six actual generation attempts are allowed across the entire assessment.

Only provider HTTP 503/504 errors are retried, with at most two retries for a failed logical call.
Equal-jitter exponential backoff sleeps between half and all of a capped exponential delay:
default ceilings are 1s, then 2s (4s cap). Each retry reuses that exact generation's conversation,
completed Python results and configuration; it does not restart the assessment or replay tools.
No retry occurs for 429, authentication/model/configuration errors, malformed output, other HTTP
statuses or a local network timeout without a provider 503/504 response. Exhausted limits return
an error rather than a fabricated recommendation.

The installed SDK 3.3.0 was inspected: its retry wrapper can default to five attempts when retry
options omit an attempt count. The transport explicitly sets `HttpRetryOptions(attempts=1)` on
both client and request options, so the application is the single retry owner.

| Environment variable | Default | Accepted bounds |
|---|---:|---|
| `GEMINI_MAX_RETRIES` | 2 | 0–3 retries per failed generation call; 0 disables retries |
| `GEMINI_MAX_GENERATION_ATTEMPTS` | 6 | 1–8 total attempts per assessment, including initial attempts |
| `GEMINI_RETRY_BASE_SECONDS` | 1 | 0.1–5 seconds |
| `GEMINI_RETRY_MAX_SECONDS` | 4 | At least the base, at most 10 seconds |

Invalid settings fail closed before any model call, with the request gate released. Setting a total
budget smaller than the number of stages can intentionally prevent completion. Retry configuration
does not change `GEMINI_MODEL` or the default model. Model calls on page load and hardcoded
fallback recommendations remain absent.

Limits also include eight goals per calculation, four displayed alternatives, a 150,000-character
input envelope, 1,600-character questions, six turns per conversation, twelve turns per customer
per hour and a ten-second cooldown. One application-process request runs at a time. These are
prototype request limits, not a distributed rate limiter or billing control.

Schema/response failures, timeouts, unavailable credentials, rate limits and upstream failures
produce clear errors while banking and manual goal management remain usable. Failed attempts
are persisted without a fake draft. Request IDs deduplicate identical successful or failed
requests; a reused ID with different intent or a stale context is rejected. An interrupted client
can retrieve completed drafts through history rather than assume no work occurred.

Logs contain only model/provider/SDK version, success, latency, logical/actual generation counts,
retry counts, backoff durations, sanitised per-attempt status, bounded tool counts and whitelisted
tool names, reported token usage, response IDs and schema-validation error counts. Failed requests
also persist this metadata separately from approved financial state.
Usage absence is explicit; zero reported tokens is not a claim about billing. No prompt, question,
financial context, raw provider error or key is logged by application observability.

## Persistence, scope and versions

`planner_turns` lives in the existing SQLite database, alongside the existing snapshots table.
Draft conversations do not write financial snapshots, increment approved goal versions, change
allocations or invalidate goals by creating artificial financial revisions. Records are scoped by
server-selected customer/sandbox and request ID, with bound session/goal IDs. They include the
selected goal version, all goal versions, context fingerprint, timestamp, model metadata and
validated result/calculations. At most forty recent turns are retrieved per customer/query.

Before requesting Gemini, expected revision, goal ownership and session ownership are checked.
After network work, the UI scope/session and current financial fingerprint are rechecked. A
changed customer, scenario, goal or financial fact rejects the result. Retrieval marks older drafts
stale. Model IDs and client-supplied financial context are never accepted as authoritative.
Server associations appear in REST results for UI use, not in the model context.

## REST endpoints

Existing mock-session, same-origin, cookie and scenario-scope protections remain intact.

| Method | Endpoint | Behaviour |
|---|---|---|
| GET | `/api/planner/status` | Key configuration, selected model, draft-only status and call limits; no inference |
| POST | `/api/planner/assess` | New assessment or conversational follow-up; draft only |
| GET | `/api/planner/sessions` | Scoped recent turns with stale status |
| GET | `/api/planner/sessions/{session_id}` | Scoped conversation history |
| GET | `/api/planner/drafts/{draft_id}` | Scoped draft with current stale status |

POST body (send the actual revision and `X-Context-Scope` from overview):

```json
{
  "goal_id": "demo-holiday",
  "question": "Can I still reach this goal without touching protected savings?",
  "request_id": "a-new-client-uuid",
  "expected_revision": 1,
  "session_id": null,
  "protected_categories": ["Groceries"]
}
```

The response envelope has `draft_id`, `session_id`, `request_id`, `goal_id`, `goal_version`,
`goal_versions`, `financial_version`, `context_schema_version`, `generated_at`, `status`,
`activation_allowed`, `validation_status`, `model_generated`, `metadata`, `result`,
`calculations` and `baseline_calculations`. `PlannerResult` contains objective, answer,
assessment, change recommendation, recommended calculation ref, strategies, assumptions,
missing information and relevant shock refs. The backend, not Gemini, supplies authoritative
IDs, versions, timestamps and numeric calculation results.

Stale revision/context/scope returns 409, malformed input 422, invalid references 400,
configuration failure 503, timeout 504, provider failure 502 and request limits 429. There is
no planner activation or approval endpoint.

## React

Goal Planner retains financial facts, shock cards, current approved goals, creation, manual
proposal review and goal history. `GeminiPlanner` adds selected-goal conversations, loading/error
states, calculated alternatives, follow-ups, temporary grocery protection, draft labels and stale
warnings. It displays contribution, schedule, deadline, completion estimate and total goal demand.
The API controller aborts/ignores late responses when customer/scenario/goal/revision changes.
It never posts to the approval path. Model content is rendered as React text, never HTML.
An explicitly injected test transport is labelled "Simulated planner test output", not Gemini.

## Evaluation and live evidence

`evaluation/planner_scenarios.py` defines fifteen contrasting cases: canonical normal/paid/unpaid,
missing/reduced/delayed income, planned and affordable/straining purchases, irregular income,
sustained essential costs, competing goals, an unrealistic deadline, no material change and
protected essentials. Each has an authored expectation. Evaluation inspects tool-supported
options, preserved source allocations, unrequested extra shocks, meaningful numerical differences,
liquidity language and deadline problems; human review is still required for reasoning quality.
These diagnostics never approve a draft.

```bash
# From backend: offline expectations only, no API calls
python -m evaluation.run_planner

# Optional, bounded real API evaluation; default interval respects free-tier use
python -m evaluation.run_planner --live --cases normal vet_paid vet_unpaid --interval 30
```

Live generation was attempted with the configured real key/model. Authenticated model lookup
succeeded. Initial normal probes failed; the diagnostic probe explicitly reported HTTP 503 (high demand). The bounded
three-case evaluation then failed normal/paid-vet upstream and timed out on unpaid-vet.
**No successful real Gemini planning output was obtained, so no live planning-quality pass or
real-world accuracy is claimed.** A final minimal connectivity generation also failed upstream. No different model was substituted. `examples/planner/live-results.json`
preserves outcomes; `*_calculation_only.json` files are clearly labelled arithmetic examples,
not AI recommendations. Mocked automated tests validate orchestration and contracts, not genuine
reasoning quality. Optional live evaluation should be rerun when the configured provider recovers.

## Verification and deferred work

Use `python -m unittest discover -s tests -v` from backend, and `npm test` / `npm run build` from
frontend. The new backend tests cover SDK configuration, secret handling, context v1.2, real read-only
arithmetic, alternative composition, insufficient information, invalid output, call limits, customer
isolation, history, stale contexts, failure persistence and blocked activation. Mocked fixtures are
explicitly marked and exercise all financial scenario inputs without claiming AI-quality evidence.
Frontend controller tests cover loading, errors, drafts, alternatives, follow-ups, unavailable Gemini,
late scope responses and the absence of activation requests. Full final results are in the delivered
implementation report.

Deferred: independent semantic/financial Safety Checker, activation of Gemini drafts, complete future
balance forecasting, production authentication/auditing, distributed quotas, richer approved contribution
schedules/priorities and real-bank validation. Calendar/start-date and future-income assumptions need
customer confirmation. Current input size and goal limits suit the hackathon prototype. No production
financial advisory certification or guaranteed affordability is claimed.


### Bounded-retry live verification

A later focused verification used `GEMINI_MODEL=gemini-3.5-flash-lite` only in its process.
One normal-holiday assessment made four logical generation stages and six actual attempts:
two calculation requests and a finish request succeeded; final structured output returned
provider 503 on its initial attempt and both retries. The final attempts had identical state
hashes, retained both completed Python calculations and did not replay them. Total API assessment
time was 20.131s; backoff totalled 2.638636s. Approved financial state was unchanged, no draft
completed, and veterinary runs were skipped because normal did not succeed. No additional live
retry/session was started. The original model configuration remains unchanged. Thirty-three
focused planner/retry regression tests passed; live completion remains blocked by provider
availability rather than being claimed successful from mocked tests.


### Optional unconstrained JSON final output

`GEMINI_FINAL_OUTPUT_MODE=structured` remains the default. Setting it to `json_text`
changes only the final generation: no API response MIME type or constrained schema is sent.
The existing PlannerResult JSON Schema is provided as output instructions instead. The entire
completed tool conversation, Python calculation ledger, reasoning instructions, request budgets
and approval protections are retained. This is an explicit selectable mode, not an automatic
fallback or a second planning session. Invalid settings fail closed.

Both modes parse the response directly with the existing Pydantic schema, reject extra/missing
fields and unknown calculation/shock references, and never repair JSON or fill missing values.
Invalid output returns `planner_invalid_response` with safe validator error categories; it is
not stored as a successful draft. Metrics identify the selected mode and final generation stage.
The server continues to own customer/goal associations, timestamps, versions and numeric results.

A focused `json_text` live check subsequently completed normal-holiday and vet-unpaid drafts
with `gemini-3.5-flash-lite` as a process-only override. Both passed PlannerResult validation,
retained real Python calculations, and were blocked at the existing activation endpoint.
Vet-paid exhausted three tool rounds without a valid calculation and correctly returned an
error, with no final draft. Across these three assessments there were eleven generation calls,
zero retries and no 503s. Forty-three focused regression tests passed. Schema validity and
arithmetic agreement do not validate every narrative claim: completion was described as exactly
at the deadline despite Python computing one day earlier, and unpaid-vet cash-buffer wording
needs human review. No independent Safety Checker is present.

### Tool recovery and final narrative precision

Calculation validation now returns at most four safe field/code/requirement issues plus the
remaining existing tool-round count. Model values and unsupported field names are not echoed.
Strict schema types, goal scope, target allocation and deadline bounds are unchanged; rejected
requests create no calculation reference. Valid prior results and thought signatures remain in
history while Gemini corrects arguments. There are no extra rounds, retries or preset strategies.
Only an aggregate validation-error count is added to normal logs.

A subsequent paid-vet diagnostic did not reproduce its earlier argument rejection: three real
calculations succeeded, but the final unconstrained call emitted `finish_planning` instead of
JSON. Final configuration now explicitly sets function-calling mode NONE and disables SDK auto
function execution, with a final-phase system instruction. Any unexpected final function call is
rejected; it is neither executed nor accepted as a draft. Google documents NONE as prohibiting
function calls: https://ai.google.dev/gemini-api/docs/function-calling .

The model is instructed to quote Python completion dates, requested deadlines, monetary values
and feasibility distinctions without inventing arithmetic, buffers or guarantees. Final instructions
repeat a compact date ledger. A narrow local contract check rejects exact/on-deadline claims
when the linked Python dates differ, rather than rewriting the recommendation. This is not an
independent financial/semantic Safety Checker and cannot establish arbitrary prose correctness.
The original argument-failure root cause remains unverified because those earlier rejected
arguments were not retained; no scenario-specific arithmetic or tool-schema relaxation was added.

The bounded quality verification made three paid-vet assessments (twelve generations, zero retries).
Each completed three valid Python calculations. Baseline final output was a function call; the
first post-fix result was real JSON but rejected by an initially overbroad precision check. That
false positive was corrected and regression-tested. The last paid-vet final request returned 503,
so live testing stopped and normal/unpaid reruns were skipped. No accepted live draft is claimed
for this quality investigation. All fifty final planner/quality/retry regressions passed.

### Bounded proposal correction and full revalidation

The same Gemini agent can correct a recoverable final-output failure without restarting its
financial planning session. `GEMINI_MAX_CORRECTIONS` defaults to 2; accepted values are 0–2.
Zero restores immediate rejection. This is a maximum, not additional budget: corrections share
the existing four logical generations, shared six-attempt default, eight tool calls and 100-second
execution deadline. A run that uses three calculation rounds plus initial final generation has
no remaining correction call and fails safely with `planner_limit`. No limits were increased.

`proposal_validation.py` applies the unchanged Pydantic contract, authorised goal/event/calculation
reference checks and local consistency checks to the **complete** proposal on every iteration.
Recoverable categories are response format/schema problems without forbidden fields, inconsistent
known calculation references, deadline precision, explicit completion-date, monetary and feasibility
claims. Unknown goal/event/calculation references, unsupported fields/actions, attempts to supply
backend-owned calculations, missing authoritative data and duplicate JSON fields are non-recoverable,
with one narrow encoding exception: an extra top-level boolean `change_requested` is still rejected
by the proposal schema, but receives recoverable `proposal_schema` feedback asking Gemini to remove
it. It is not an accepted field or action. Feedback prohibits renaming/copying it into
`change_recommended`, which independently describes a recommendation rather than user intent.
The final prompt makes that distinction explicit. Nested or non-boolean versions, other unknown
fields and mixed forbidden actions remain non-recoverable. Only a complete model-generated revision
that passes all checks can become an unverified draft; Python never silently strips this field.
A failed correction introducing a forbidden field is rejected immediately rather than grandfathered.

Correction messages contain the original output as untrusted data, typed field-level issues, original
claim, linked calculation ref, authoritative values, the complete successful Python ledger and date
facts with Python-derived `before`, `on`, `after` or `unknown` relationships. Existing model Content,
thought signatures, function responses and calculation refs are preserved. Neither final nor correction
calls permit function calling; both structured and json_text modes retain their encoding/configuration.
No Python defaults are filled into malformed model output and no narrative is silently rewritten.

Corrections reuse existing calculated schedules. A new financial schedule must already have a matching
successful Python result before it can be accepted. Structured attempts to introduce new financial
parameters/calculations are rejected; arbitrary new parameters cannot be extracted from prose and
calculated implicitly. Such a strategy needs a separately calculated planning assessment. There are
no extra calculation rounds inside wording correction and no strategy menu or fallback recommendation.

Metrics record initial validity, logical correction attempts, safe category names, correction success,
reference-set changes versus wording-only changes, missing additional-calculation evidence, actual
calculation/generation counts and final outcome. If malformed initial JSON prevents establishing the
original reference set, strategy-change status is unknown. Prompts, claims, financial values and field
feedback are not added to ordinary logs. Upstream errors retain their provider/timeout classification;
existing bounded provider retries remain unchanged and share the same total budgets.

After full local validation, PlannerService still checks live scope/current financial fingerprint and
the deadline before storing the sole final `draft_unverified`. Failed/intermediate outputs are never
saved as successful drafts. No activation route or independent AI Safety Checker is introduced.

These are deterministic local contract checks, not general natural-language verification. They cover
recognisable contribution/frequency and surplus claims, unsupported explicit currency values, ISO date
claims, exact-deadline wording and explicit deadline/capacity assertions. They do not establish arbitrary
semantic correctness, feasibility of all real-world actions or independent financial safety. All drafts
retain the existing unverified status and require human review.

Correction workflow verification: all 176 Python tests and 15 frontend tests passed; the production
frontend build passed after restoring the exact locked macOS ARM64 native Rollup/esbuild packages
(the pre-existing dependency directory contained Windows binaries). No frontend source or lockfile
changed. One real normal-holiday run used process-only gemini-3.5-flash-lite/json_text with retries
disabled. Two Python calculations reproduced exactly, followed by finish_planning; final generation
returned upstream 503 after 6.080s. There was no initial proposal to validate, zero correction attempts,
no draft and no financial-state change. No retry or additional scenario was attempted. Deterministic
correction success is not presented as live correction success.

## Current provider resilience and explicit resumption

This section supersedes the earlier operational retry/deadline limits above. Historical live
verification records describe the settings used at their time, not the current defaults.

| Backend environment variable | Default | Accepted range |
|---|---:|---|
| `GEMINI_MAX_RETRIES` | 4 | 0–6 retries per failed logical generation |
| `GEMINI_MAX_GENERATION_ATTEMPTS` | 20 | 1–32 attempts per explicitly requested execution |
| `GEMINI_RETRY_BASE_SECONDS` | 5 | 0.1–30 seconds |
| `GEMINI_RETRY_MAX_SECONDS` | 40 | At least the base; at most 120 seconds |
| `GEMINI_EXECUTION_DEADLINE_SECONDS` | 300 | 30–900 seconds, including context preparation |
| `GEMINI_MAX_LOGICAL_GENERATIONS` | 6 | 2–8, cumulative across resumes |
| `GEMINI_MAX_SESSION_ATTEMPTS` | 40 | 1–96 attempts across all executions of one request |
| `GEMINI_MAX_RESUMES` | 2 | 0–3 explicit resumes per request |
| `GEMINI_MAX_CORRECTIONS` | 2 | Unchanged: 0–2, cumulative across resumes |

Three calculation rounds/eight function calls remain unchanged. Six default logical generations
allow three tool rounds, initial final output and two corrections. The SDK still has attempts=1
at client and request levels. No model default or financial formula changed.

HTTP 503/504 retry the exact failed generation/history/configuration, using exponential ceilings
5/10/20/40 seconds and jitter uniformly between 80% and 100% of each ceiling. Valid numeric or
HTTP-date Retry-After is a minimum wait, including zero; invalid/negative/non-finite hints are ignored.
A hint may exceed the ordinary backoff cap if it fits the overall deadline. If it cannot fit, the
request is interrupted and its not-before time prevents an early explicit retry. Network calls
remain capped at 30 seconds and clipped to the execution time remaining. A short cancellable wait
loop is used in production; automated tests inject clocks/waits.

Invalid credentials, unsupported models and invalid request schemas are not retried. A 429 is
automatically retried only with valid Retry-After and no zero-quota evidence. Zero quota is never
retried or made resumable. A nonzero rate limit without valid guidance is not automatically retried;
checkpointed work can be resumed explicitly after a conservative 60-second minimum. A local network
timeout is not automatically retried but can preserve a checkpoint for explicit resume. Provider,
validation, cancellation and budget errors keep separate classifications and counters.

### Persistence and status API

Existing synchronous POST `/api/planner/assess` stays compatible. `background: true` returns HTTP
202 with a request ID promptly, and a single in-process worker executes the same agent. No external
queue, service or alternative planner is introduced. Existing SQLite `planner_turns` payloads now
hold private checkpoints and safe progress. Atomic customer/request claims, execution tokens and
lease checks prevent duplicate execution/drafts for the same request.

- GET `/api/planner/requests/{request_id}`: customer/scope-bound safe progress and, only after full
  validation/current-context checks, the unverified draft. No raw checkpoint/prompt is exposed.
- POST `/api/planner/requests/{request_id}/cancel`: request cooperative cancellation or discard an
  interrupted checkpoint. Cancellation is idempotent; completion already committed remains completed.
- POST `/api/planner/requests/{request_id}/retry`, body `{expected_revision: ...}`: explicit resume.
  Current financial fingerprint, goal versions, authorised customer/goal, model identity and scope
  are checked before any provider call. Stale checkpoints are discarded and require a fresh assessment.

All routes use the existing session/scope guards. Background work runs outside the UI session lock.
Regular progress polling uses cheap revision reads; expensive financial fingerprints are checked at
execution/resume and before publication, not on every active progress poll. Goal/financial revisions
and cancellation are also checked during waits. The original twelve-turn/hour and cooldown limits
remain in place; resumes exclude their own request row and have their additional finite resume cap.

Checkpoints contain successful calculation refs/results, relevant conversation Content/function
responses/signatures, pending request configuration, received responses, processed tool-batch index,
correction state and cumulative counters. SDK HTTP options/headers/credentials are excluded. An
explicit typed JSON codec preserves opaque byte signatures without tag collisions; snapshots have a
2 MB bound and a credential check. They contain the existing minimised financial context necessary
for resumption, not raw credentials or a new bank ledger. Successful, cancelled, permanent-failure
and stale runs discard checkpoint data; interrupted runs retain it for resume/discard in the local DB.

A resumed pending generation does not consume another logical-generation slot. A received response
or already checkpointed tool result is reused, not requested/computed again. Validation of the full
proposal and final context/goal-version checks still run. Changed financial parameters need matching
Python evidence; no correction or retry can modify the protected ledger or activate a goal.

### Frontend and cancellation

The existing planner UI submits background jobs, shows generation/retry/wait states, attempt and saved
calculation counts, and uses an indeterminate progress indicator. It polls about every 2.5 seconds
while generating and 5 seconds while waiting, stopping on a terminal result or connection failure.
Interrupted rows offer explicit resume/discard. Deadline exhaustion is distinguished from availability.
A lost submission/progress response retains its request ID and warns that the backend may still be
running; Check status retrieves that same request rather than generating another plan. Reload can
reattach to an existing active request using GET only, without starting new inference.

Cancel and UI disposal send an explicit best-effort keepalive cancellation request. Aborting a browser
poll is not treated as backend cancellation. Waiting cancels promptly; an in-flight SDK request cannot
be force-killed and may take up to its 30-second timeout. Its returned response is checked for cancellation
before tools or draft publication. Scope changes also stop further work. No success is displayed until
the complete corrected proposal and current context have passed all checks.

### Practical limits

This remains a one-process prototype worker, with SQLite ownership/leases rather than a distributed
job system. After process loss, the UI waits for the recorded lease to expire before explicit resume.
A crash during an uncheckpointed pure read-only calculation can require recomputing that calculation;
already checkpointed results are not replayed. Checkpoints/contexts/signatures are private local state
and retained until a terminal result or explicit discard; the existing prototype storage/authentication
model still applies. Provider retry exhaustion, overall deadline, cumulative call limits or explicit
resume limits can still interrupt/fail a request. No autonomous retry continues indefinitely.

The local proposal validator remains strict and distinct from the deferred independent Safety
Checker. All accepted results remain `draft_unverified`, activation_allowed=false, and cannot use the
existing goal approval path. Passing local contracts does not establish arbitrary prose correctness.

### Surplus terminology

The agent distinguishes `financial_insights.cashflow.average_surplus` (historical monthly net flows,
before a proposed contribution), `comfortable_capacity`/`stretch_capacity` (capacity estimates),
`one_month_comparison.monthly_surplus_after_contribution` (the calculated residual) and
`liquid_after_pending_reserve_and_contribution` (a projected one-month cash balance). A capacity
estimate is not a remaining surplus; equal source values do not make these concepts interchangeable.
Instructions require explicit historical versus post-contribution wording and each strategy's own
Python results. Surplus correction feedback includes historical surplus and capacity estimates as
well as the unchanged calculation ledger.

The local wording check recognises the explicit qualifier “historical average monthly surplus”.
In “monthly surplus after contribution is AUD …”, the amount is checked as a residual, rather than
also incorrectly as a contribution. Separate contribution claims, unqualified surplus claims and
incorrect historical/residual values still receive full checks. No financial formula or proposal
field was changed; corrections remain bounded model revisions against existing Python evidence.

### Source-labelled facts and explicit narrative contradictions

`planning/financial_facts.py` projects existing minimised context/calculation values into facts;
no financial formula or recommendation is added. Each fact has a metric, exact value, unit,
period/date, meaning, source path, calculation reference (null for context facts) and assumptions
or a reference to shared calculation assumptions. Unknown values are omitted. Monthly averages
identify their actual source month buckets. Current funding-account balance, runway projection,
cash after known obligations, post-contribution monthly surplus and post-contribution cash balance
are distinct facts. The one-month contribution comparison is explicitly not a full future forecast.
Expense facts preserve status and whether the expense already affects a balance/projection.

The final prompt and every correction receive `authoritative_financial_facts`, alongside the unchanged
ledger. `planning/narrative.py` checks identifiable cash sign/amount claims, flow/stock conflations,
explicit current versus projected balances, cash-funded/no-shortfall assertions, named completion
and deadline dates/relationships, and known paid/outstanding/double-debit assertions. Scope is the
strategy ref or explicit calculation ref in a clause. Unscoped mixed outcomes require consensus
(or explicit “both/all”) rather than assigning one strategy's outcome to another. Existing contribution,
capacity, schema, reference and arithmetic checks remain in force. A cash claim cannot be justified
by capacity alone. Negative values retain their sign with `AUD -300`, `-$300` or Unicode minus.

Recoverable contradictions use the existing issue contract: precise field, problematic statement,
source-labelled authoritative facts and required correction. Gemini revises the complete proposal;
Python never rewrites recommendations. The same two-correction ceiling, calculation ledger, call/time
budgets and draft/activation restrictions apply. Changing financial parameters still requires existing
matching calculated evidence; no extra calculation phase or independent Safety Checker is introduced.

These are bounded deterministic checks, not arbitrary prose understanding or a comprehensive safety
policy. Hypothetical/different future periods and negated statements are skipped conservatively;
unknown or ambiguous references/metrics are not guessed. Passing these checks does not select the
best strategy, validate speculative income, establish affordability in all future periods or confer
independent approval. Financial planning quality must be reported separately from technical validation.
