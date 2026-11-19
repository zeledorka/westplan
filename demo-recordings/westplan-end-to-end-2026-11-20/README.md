# Fresh WestPlan browser walkthrough — 20 November 2026

No video was recorded: the connected browser exposes no screen-recording capability. This is the requested sequential-screenshot fallback. All 15 JPEG files were decoded successfully at 1440×900.

Working frontend: http://127.0.0.1:5179/#demo. Backend: http://127.0.0.1:8012. Branch: feature/deepseek-financial-planner.

A new isolated SQLite database was used, with no prior goals, approvals, presenter events, notifications or planner requests. Original customer records and .env hashes remain unchanged.

Jake created a $50 goal with $0 allocation and a $50 fortnightly contribution due2026-11-29 through the UI. Creation independently PASSed. One $1,500 unpaid bill was triggered through the UI; its notification and one DeepSeek assessment started automatically. Real Python calculations, strict schema/narrative validation and the independent checker ran. Final outcome:PASS, maintain original schedule. Accept was clicked through the browser; one sandbox approval persisted and approval-time safety recheck PASSed. Goal version 1→2, with target, allocation, contribution and deadline unchanged. Reject was visible but was not executed on the accepted result. No real banking transaction occurred.

Four real DeepSeek requests; three calculations; zero corrections/revisions. Usage: 75,293 prompt + 1,079 completion tokens. Reported cost: US$0.015322295; conservative accounting: US$0.020364087, below US$0.25.

Shock click→backend completed draft: 134.37s. Completion was visibly captured by 199.54s after the click; this is an observation upper bound. Model execution: 71.19s.

One confirmed display defect was fixed in GeminiPlanner.jsx: an approved result is now labelled as historical independent PASS after the goal version changes, instead of misleadingly “not assessed.” Stale unapproved drafts remain blocked.24 frontend tests, 5 render checks and production build passed.

## Remaining model/explanation issues

- Model applied an additional hypothetical $1,500 expense although a $1,500 bill was already recorded. This is conservative stress evidence, not a second posted transaction; the narrative does not distinguish it accurately.
- Model wording says a 90-day projection although the recommended calculation ends 2026-11-29, 14 days from assessment.
- Model says protected reserve $0 (the optional extra calculation input), without distinguishing the separately preserved savings reserve $10,105.55.
- Backend/context loading and result polling introduce visible latency; some decision controls need a short scroll within the existing phone-width layout.

These issues were preserved in the actual model output and full evidence. No formulas, Safety Checker, numerical tolerances or generated content were changed. The model’s extra hypothetical stress is more conservative; the observed PASS and approval are the actual backend results, not a guarantee of unrestricted narrative accuracy.

## Browser action timeline

- 03:05:19 AEST — [01-fresh-login](01-fresh-login.jpg)
- 03:06:25 AEST — [02-clean-goal-area](02-clean-goal-area.jpg)
- 03:06:29 AEST — [03-clean-banking-dashboard](03-clean-banking-dashboard.jpg)
- 03:06:38 AEST — [04-reserve-confirmation](04-reserve-confirmation.jpg)
- 03:07:42 AEST — [05-goal-entry](05-goal-entry.jpg)
- 03:08:32 AEST — [06-approved-goal-schedule](06-approved-goal-schedule.jpg)
- 03:08:32 AEST — shock-click
- 03:08:33 AEST — [07-shock-submitted](07-shock-submitted.jpg)
- 03:09:11 AEST — [08-automatic-shock-notification](08-automatic-shock-notification.jpg)
- 03:09:38 AEST — [09-automatic-ai-progress](09-automatic-ai-progress.jpg)
- 03:10:49 AEST — [10-python-calculations-and-final-generation](10-python-calculations-and-final-generation.jpg)
- 03:11:52 AEST — [11-automatic-recommendation-pass](11-automatic-recommendation-pass.jpg)
- 03:12:00 AEST — [12-genuine-model-recommendation](12-genuine-model-recommendation.jpg)
- 03:12:00 AEST — [13-sandbox-accept-submitted](13-sandbox-accept-submitted.jpg)
- 03:13:34 AEST — [14-approved-result-before-display-fix](14-approved-result-before-display-fix.jpg)
- 03:14:18 AEST — [15-approved-and-applied-in-sandbox](15-approved-and-applied-in-sandbox.jpg)

The current backend is the bounded test runtime: it retains this fresh database and refuses a second assessment. Restart the normal backend with an explicitly chosen test database for another live run. No application defaults or .env values were changed for this test.
