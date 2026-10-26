# Offline availability baseline

`tornredeye.availability_forecast` compares an expanding historical availability
probability with last observed state persistence. The operation selects a country
and item explicitly, with no country/item defaults.
Python 3.11 and the standard library suffice. The command reads existing history,
prints deterministic private JSON, and performs no network or archive operations.

## Inputs and usage

Reuse a previously validated [stock-history report](stock-history.md) in JSON,
with its country/item metadata and `observations`. Existing selected-item JSONL
research outputs are also accepted: each row must identify `country`, `item_id`,
`evidence_key`, `retrieved_at_utc`, `source_timestamp`, `quantity`, and `issues`.
The CLI selects matching country/item rows from JSONL; a JSON report must match
the requested selection. The Python entry point is
`evaluate(history, country=..., item_id=..., attempts=...)`, using the JSON report
shape. This consumes existing normalization results; it does not normalize or
reverify backup archives or authenticate user-supplied history.

Optionally supply the existing normalized-attempt JSONL index with `--attempts`.
Its `key` and original `retrieved_at` identify each collection attempt;
`document_status` distinguishes recorded collection failures from other absent
target evidence. Matching raw-history/attempt records are joined by original
collection key and must agree on retrieval time. Additional attempts carry no
target value. Operational attempts use the matching `evidence/live-v1/` retrieval-hour keys.
Use the preserved original code/configuration for Issue #49's sealed historical
cohort. Its frozen rules below are unchanged; new-lineage results are a separate
cohort and do not establish historical continuity.

All timestamps must be timezone aware. Unplaceable evidence, simultaneous
retrievals without established order, duplicate identities, mismatched retrieval
context and history warnings fail explicitly. Missing quantities and invalid
source timestamps remain unavailable observations. Do not manufacture missing
context or substitute file modification times.

For example, select Hawaii Shark Fin (`haw`, `1485`) from the repository root in
PowerShell:

```powershell
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand python @('-B', '-m', 'tornredeye.availability_forecast', 'C:\private\history.jsonl', '--attempts', 'C:\private\normalized-attempts.jsonl', '--country', 'haw', '--item-id', '1485')
```

Output contains private evidence keys, quantities and input identities. Capture
it outside Git, using a new derived-output path. Exit 0 means a report was
produced, even when insufficient evidence leaves probabilities/scores null.
Exit 2 indicates invalid or unreadable input; no substitute forecast is emitted.

## Frozen observation and target rules

These rules preserve Issue #49's experiment, whose initial research case was
Hawaii Shark Fin. They apply to the caller-selected country/item; the fixed
hourly grid and horizon are experimental settings, not universal product rules.

- Origins are fixed hourly UTC boundaries, from the ceiling of the first retained
  retrieval through the last retained retrieval. Missing hours remain visible.
- The target is origin plus 60 minutes. At origin and target, use the latest
  eligible source report retrieved by that time, with source age **at most 300
  seconds**, measured against the source timestamp. Cached receipts do not renew
  this age. No observation after the decision time is used.
- Valid observations require the retained validation/context checks and a
  nonnegative integer quantity, with a known, nonfuture source timestamp.
  Failed/missing attempts may retain an earlier eligible report within the age
  limit, explicitly flagged. They never supply a zero quantity.
- Detect source-time quantity contradictions as they become observable, rather
  than using retrospective history flags to exclude earlier observations. A known
  conflict at the latest source time blocks selection until a strictly newer
  unambiguous update; no older fallback hides it. Regressing source reports do not
  replace the more recent source state, but can reveal a contradiction of a past
  report.
- A resolved target is `quantity > 0` in the selected report. Its source timestamp
  must be after the origin. Thus the source snapshot may describe approximately
  55–60 minutes after origin, rather than exact in-game stock at minute 60.
  Targets beyond the known evidence cutoff, stale targets and conflicting or
  unavailable targets remain unresolved.

## Chronological training and evaluation

At each eligible origin, the empirical probability is the unsmoothed positive
fraction of **earlier origin-eligible hourly labels resolved at targets no later
than that origin**. Every resolved eligible hourly-origin label has equal weight.
There is no learned conditioning, selected training window, prior probability or
threshold tuning. A label maturing exactly at an origin can be used then; an
earlier receipt does not mature the label before its target time.

With no resolved historical labels the empirical forecast abstains. The resulting
resolved outcome can seed later training even though that bootstrap origin had no
empirical forecast. Origin-ineligible and unresolved rows never become negative
training examples. Persistence assigns probability 1 or 0 from the origin's
eligible reported stock state.

Compare both methods only where the origin is eligible, the empirical history is
nonempty, and the target resolves. The Brier score is the mean squared difference
between probability and the binary reported outcome; lower is better. Report
common scored counts, origin/label exclusions, training counts, evidence endpoints
and daily paired scores. With no common scored origins, scores remain null.

Later contradictions are annotated separately. Labels resolved using information
available at their target remain in training and scoring as originally resolved;
they are not selectively excluded or revised using future knowledge. The report
lists contradiction discovery times and affected label counts.

This is an initial chronological experiment on previously explored, short,
single-source history. Hourly origins and distinct source times do not establish
independent samples; serial dependence, informative missingness and the source
proxy limit interpretation. Daily scores are descriptive, not an untouched test
or an IID uncertainty estimate. No independently validated performance, calibrated
probabilities, exact restock timing or actual in-game availability is established.

## Checks

The separate [offline prospective accounting core](shadow-forecast.md) reuses the
baseline probabilities and observation selection. It does not attest live issuance
or change this historical evaluation contract.

```text
python -B -m unittest discover -s tests -p test_availability_forecast.py
python -B -m unittest discover -s tests
git diff --check
```

## Requested-time offline comparison

`tornredeye.requested_availability` is a separate retrospective evaluator. It
reuses the frozen baseline's input validation, chronological source-state
selection and probability calculation without changing that experiment or the
operating shadow pilot. It accepts explicit origin/target UTC pairs and a required
maximum source age, with no automatic origin schedule or universal horizon.
The target is positive quantity in an eligible source report at the requested
time, not actual in-game availability, replenishment or purchasing success.

The Python API is
`evaluate(history, requests, country=..., item_id=..., maximum_source_age_seconds=..., attempts=(), available_at=None)`
in `tornredeye.requested_availability`. Use a previously verified stock-history
JSON report for the selected country/item. Optional `attempts` use the existing
normalized-attempt contract. Requests are a nonempty JSON list, for example:

```json
[
  {"origin_utc": "2026-10-24T00:00:00Z", "target_utc": "2026-10-24T00:10:00Z"},
  {"origin_utc": "2026-10-24T00:00:00Z", "target_utc": "2026-10-24T01:00:00Z"},
  {"origin_utc": "2026-10-24T01:00:00Z", "target_utc": "2026-10-24T01:10:00Z"},
  {"origin_utc": "2026-10-24T01:00:00Z", "target_utc": "2026-10-24T02:00:00Z"}
]
```

These are example research requests, not claims about available evidence. For
example, select Hawaii Shark Fin from the repository root, with already-local
private inputs:

```powershell
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand python @('-B', '-m', 'tornredeye.requested_availability', 'C:\private\history.json', 'C:\private\requests.json', '--country', 'haw', '--item-id', '1485', '--maximum-source-age-seconds', '300')
```

For a comparison across country/item pairs, use the same requests with separate
matching histories; Japan Xanax (`jap`/`206`) is another example. Do not pool item
histories. For example, repeat with 180 seconds for exploratory sensitivity. The
10/60-minute offsets and 300/180-second limits are research settings, not production
defaults. Comparisons between freshness settings may have different eligibility;
inspect coverage and match individual pairs before interpreting score differences
across settings.

- By default, availability is proxied by original retrieval time, explicitly
  labelled `retrospective_retrieval_time_proxy`. Optional `--available-at` supplies
  a JSON mapping from every input raw/attempt key to downstream availability time;
  mappings must be complete and cannot precede retrieval. Original retrieval and
  source times remain separate. Simultaneous deliveries are processed together
  by the cutoff, ordered by original retrieval time. Supplied availability times
  are caller assertions, not verified live issuance. Restored file times cannot
  establish historical availability. A matched attempt supplies no new stock value
  until its raw counterpart becomes available; a previous fresh report may carry
  through that unknown attempt. When delivered together, validated raw data
  takes precedence over the same collection's unknown index entry.
- Origins and outcomes use only inputs available by their respective times.
  Source age is measured at the decision time. Targets must follow origins and
  require a source update after the origin. Targets beyond the last input
  availability remain outside the evidence cutoff. Cached reports never renew
  source age. An unavailable or regressing attempt may carry the previous valid
  source report only within the configured age limit, explicitly marked
  `carried_over_unavailable_attempt`. It never supplies a zero value or renews
  the report's source age. No valid prior report means no observation; an expired
  report remains stale. Known conflicts block selection without older fallback; later
  contradictions are audit annotations and never rewrite earlier decisions.
- Persistence uses the eligible origin's positive/zero state. Historical probability
  is the unsmoothed positive fraction of earlier origin-eligible requested outcomes
  with target times strictly before the current origin. A target exactly equal
  to the origin is excluded from training, even if its source report is already
  available: there is no proof outcome accounting completed before issuance.
  This retrospective convention allows strictly earlier targets without imposing
  an arbitrary processing delay, but cannot prove actual accounting completion.
  It may omit equal-time labels that a real consumer processed first, and may
  admit earlier labels a delayed consumer had not yet processed. Live evaluation
  requires actual resolution/readback timing. The frozen evaluator's inclusive
  boundary and the live pilot's accounting behavior remain unchanged. Each target
  source timestamp enters training once across all requests, including cold-start
  outcomes. This prevents cached/repeated labels multiplying training weight, but
  does not establish independence or an elapsed-time prevalence estimate. A request
  list affects this baseline's training sample. No resolved training labels means
  empirical abstention, not an invented prior probability.
- Scores use exactly the same eligible origin/target pairs for both methods,
  excluding empirical cold starts and unresolved labels. Output includes paired
  Brier scores, positive/zero outcomes, exclusions, abstentions, per-offset summaries,
  source ages, evidence endpoints and training-label references. Each record's
  `training_count` selects the prefix of the returned `training_labels` available
  then. CLI inputs carry file hashes; evidence and output remain private. Exit 0
  can report no scored pairs with null scores; invalid inputs exit 2 without a report.
  Error diagnostics name the input stage and a safe reason (including JSON line/
  column or an allowlisted validation failure), without echoing paths, evidence
  keys, input values or raw exception messages.

This is retrospective replay, even with supplied availability times; it does not
create genuinely issued forecasts or establish independent accuracy/calibration.
Overlapping requests, repeated source states and unresolved outcomes make scores
dependent and potentially selective. For newer-evidence evaluation, predeclare
requests after the explored audit endpoint `2026-10-23T08:40:31Z` and retain verified
coverage/provenance. Do not describe that explored sample or data used to select
settings as untouched validation. This command performs no evidence download,
restoration or cloud access. Without newer verified inputs, synthetic tests verify
software behavior only; actual predictive performance remains unmeasured.

Focused validation: `python -B -m unittest discover -s tests -p test_requested_availability.py`.

## Private requested-time accounting

`tornredeye.requested_forecast.RequestedForecastRun` adds an in-memory operation
for a selected country/item and an explicitly requested future UTC target. It
reuses captured evidence and the existing simple baselines, but takes issuance
from the processing clock instead of accepting retrospective origins. There is
no fixed horizon, hourly slot, scheduler or cloud adapter. See the
[requested-time accounting contract](shadow-forecast.md#requested-time-offline-accounting)
for the API, request identity, outcome ordering and live-integration boundary.

Unlike retrospective replay's strict target-before-origin training convention,
this operation records completed outcome-accounting calls. Only those outcomes
can train subsequent decisions, including operations ordered at an equal clock
timestamp. Both interfaces predict positive source-reported stock, preserve
unresolved outcomes and abstain when empirical training is unavailable; neither
establishes successful purchasing or calibrated live accuracy. The frozen
historical evaluator and active Hawaii pilot remain unchanged.
