# OpenRouter alongside Gemini

The existing planner, calculation tools, technical validation, independent Python safety
checker, bounded safety revision and SQLite jobs are shared by both providers. No AI draft
can be activated, including one that passes the implemented financial checks.

## Configuration

Keep the existing backend `.env` credentials. This implementation does not change `.env`.
To select OpenRouter when you are ready, configure:

```dotenv
PLANNER_PROVIDER=openrouter
OPENROUTER_MODEL=nvidia/nemotron-3-ultra-550b-a55b:free
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

`OPENROUTER_API_KEY` must already be set on the backend. Never put it in frontend variables.
Restart the backend after changing the selected provider/model. With `PLANNER_PROVIDER`
unset or `gemini`, the existing `GEMINI_API_KEY`, `GEMINI_MODEL` and final-output settings
continue to apply. Merely configuring an OpenRouter key does not select OpenRouter.

Optional settings:

| Setting | Default | Behavior |
|---|---|---|
| `OPENROUTER_PROFILE` | `tools_json_text` | JSON-only final instructions followed by all existing local validation. Appropriate for Nemotron. |
| `OPENROUTER_PROFILE=tools_structured` | Explicit opt-in | Requires an endpoint advertising both JSON Schema output and all required tool-choice modes. Local validation still runs. |
| `OPENROUTER_REASONING_EFFORT` | `low` | Sent to endpoints advertising reasoning-effort support; supports none/minimal/low/medium/high, subject to endpoint acceptance. |
| `PLANNER_MIN_REQUEST_INTERVAL_SECONDS` | `5` | Minimum start-to-start interval for real Gemini/OpenRouter requests; integer 0–60. Applies across tool, final, correction, revision and retry stages. |
| `OPENROUTER_ALLOW_PAID` | `false` | Only exact `:free` model IDs with verified zero-price endpoints are accepted. Other model IDs require explicit `true`. |

The base URL is restricted to the official HTTPS OpenRouter API. No automatic model-router,
model fallback or provider fallback is enabled. Endpoint capabilities/prices are checked using
public, unauthenticated metadata and cached for that transport. All profiles require tools and
`required`, `auto` and `none` tool choice. Unsupported models/endpoints fail before inference;
the adapter never silently downgrades required tools or structured output. For another model,
change `OPENROUTER_MODEL` and choose a compatible profile; no code change is required. The
model must accept the actual financial tool schema as well as advertise the required features.

The free Nemotron endpoint is intended here for synthetic prototype data. Its provider terms
do not permit uploading confidential/personal data. The UI identifies OpenRouter and its model
provider as the destination for minimized context.

## Budgets, pacing and failures

Existing `GEMINI_*` orchestration budget settings retain their names and apply to both providers:
9 logical generations, 8 tool calls, 2 technical corrections, 1 safety revision, 20 HTTP attempts
per execution, 40 across the request, 2 explicit resumes and a 300-second execution deadline
by default. Provider calls use the existing 30-second network timeout (per HTTP I/O phase, not a hard wall-clock cancellation guarantee). Reasoning and completion
share each OpenRouter request's output cap (6,000 for tools; 8,000 for final/correction).
Conservative context headroom checks reject oversized requests without dropping evidence.

The planner owns all retry and pacing waits. Both SDK/HTTP adapters disable hidden retries.
A slow request already occupying the interval causes no extra pacing sleep. Waits are
cancellable, report progress and consume the shared deadline. `Retry-After` is honored;
otherwise demonstrably temporary rate limits and selected transient HTTP failures use existing
bounded exponential backoff with jitter. Unknown quota failures, exhausted daily quotas,
invalid credentials, credit exhaustion and permanent request errors are not automatically retried.
Local timeout/connection ambiguity requires explicit resume rather than automatic replay.

Checkpoints pin provider, model and OpenRouter profile/base URL/price policy/reasoning setting.
Native assistant tool messages, call IDs, reasoning metadata, completed calculations, pending
generation, partial batch position and revision/correction counters are preserved. Resume of a
safety narrative correction continues that stage; it does not recalculate prior results or start
another safety cycle. Changed identity requires a new assessment. Version-1 Gemini checkpoints
remain supported for the same provider/model/context.

Only complete validated and independently assessed drafts are published. Failures preserve the
existing original safety findings/calculation evidence; resumable failures retain the private
checkpoint. No raw provider error text or credentials are exposed. Usage includes reasoning
tokens and reported cost; missing cost is explicitly distinguished from zero. A timed-out
upstream request may have consumed tokens without reporting usage.

## Validation

Run offline tests with a temporary `BANKING_DB`, because importing the API creates its default
database. `tests/test_openrouter.py` uses `httpx.MockTransport`, the real calculation engine and
the real independent checker. It covers matched multiple/sequential tool calls, exact reasoning
replay, output/schema rejection, capability/paid-use guards, pacing/deadlines/cancellation,
transient/permanent limits, provider/model/profile checkpoint pins, legacy Gemini recovery and
durable 429 recovery during safety narrative correction without financial-state mutation.

Existing Gemini, calculation, safety, revision, API and frontend tests remain in place.

The live evaluation runner now uses the configured transport **and the independent checker**.
For one bounded synthetic experiment after configuring the provider, run from `backend`:

```sh
GEMINI_MAX_RETRIES=0 GEMINI_MAX_GENERATION_ATTEMPTS=9 \
  python -m evaluation.run_planner --live --cases normal --output /tmp/westplan-openrouter-live
```

This can generate multiple requests, including a correction or safety revision, within the
existing logical/tool/deadline ceilings. It does not resume automatically. Use only an explicitly
authorized free model for the initial experiment. A connectivity success, valid JSON or a
conditional safety PASS alone does not establish full financial narrative quality.

## Implementation verification (17 November 2026)

The full backend suite passed 265 tests; the final OpenRouter-specific suite passed all 19
tests (including three later edge cases). All 21 frontend tests and the production build passed.
Two bounded synthetic free Nemotron assessments made five model requests each, with reported
cost zero. The standard evaluation was rejected after exhausting its two monetary-claim
corrections. A focused real-service case completed after two corrections, passed the implemented
independent checks, persisted in SQLite and left financial state unchanged. Activation remained
disabled. Safety revision and 429/restart recovery were tested deterministically; no live quota
exhaustion was induced. Narrative quality remains model-dependent; rejection is a valid safe
outcome, not a reason to loosen checks. `.env` was not changed.
