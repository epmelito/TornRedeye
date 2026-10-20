# Offline availability baseline

`availability_forecast.py` compares an expanding historical availability
probability with last observed state persistence. The operation selects a country
and item; Hawaii Shark Fin (`haw`, `1485`) is the initial research case.
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
target value. The existing `normalized/yata/jap/206/` and matching raw prefixes
identify the collector's full YATA exports, including other selected countries.
They do not restrict forecasting to Japan. Omitting this index limits known
attempt coverage and the evidence cutoff to the supplied history.

All timestamps must be timezone aware. Unplaceable evidence, simultaneous
retrievals without established order, duplicate identities, mismatched retrieval
context and history warnings fail explicitly. Missing quantities and invalid
source timestamps remain unavailable observations. Do not manufacture missing
context or substitute file modification times.

From the repository root in PowerShell:

```powershell
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand python @('-B', 'availability_forecast.py', 'C:\private\history.jsonl', '--attempts', 'C:\private\normalized-attempts.jsonl', '--country', 'haw', '--item-id', '1485')
```

Output contains private evidence keys, quantities and input identities. Capture
it outside Git, using a new derived-output path. Exit 0 means a report was
produced, even when insufficient evidence leaves probabilities/scores null.
Exit 2 indicates invalid or unreadable input; no substitute forecast is emitted.

## Frozen observation and target rules

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

```text
python -B -m unittest discover -s tests -p test_availability_forecast.py
python -B -m unittest discover -s tests
git diff --check
```
