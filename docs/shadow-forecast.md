# Private prospective shadow forecasts

Issue #51 includes `tornredeye.shadow_forecast.OfflineShadowRun`: a standard-library,
in-memory accounting core, tested only with synthetic clocks and observations.
It performs no provider reads, file writes, scheduling or deployment. Its records
always say `offline_accounting_only` / `live_issuance_verified: false`; they are
**not proof that a prediction was published before its target**. There is no CLI
for backdating historical forecasts or importing a retrospective replay as issued.
Issue #49 and its frozen hourly retrospective evaluation remain unchanged.

## Core contract

- Construct a run for a country/item (default `haw`/`1485`). Production-clock
  semantics use UTC now; inject a clock only for deterministic offline exercises.
- `observe(history, attempts=...)` captures a cumulative, already validated
  stock-history report and optional normalized-attempt index, as accepted by
  [the offline baseline](availability-forecast.md). Future retrievals, simultaneous
  receipts and changed/removed captured evidence fail explicitly. An additional
  raw receipt matching a previously unknown normalized attempt may supply a value
  in a later snapshot; earlier snapshots remain intact.
- Capture time records when inputs became available to this consumer. Merely
  having a retrieval timestamp before a decision does not make a late-delivered
  input available for that decision. Snapshot IDs bind canonical rows, the original
  input digest, capture time and inventory. Exports include the canonical rows for
  reproducing selection and retain evidence keys, quantities, source/retrieval
  timestamps and validation issues. Keep the original source evidence separately.
- `issue()` takes its timestamp from the processing clock; callers cannot pass an
  earlier issuance/origin time. Its target is exactly 60 minutes later. One record
  per UTC-hour slot is allowed; duplicate slots and clock regressions fail. The slot
  is an identity/deduplication key, **not a claim of issuance at the hour boundary**.
  A delayed invocation is never backdated. Actual-time origins are a distinct
  prospective protocol; they do not change Issue #49's exact hourly grid.
- Each record retains its immutable ID, actual offline decision/target times,
  method/version IDs, snapshot/origin provenance, probabilities, eligibility and
  training outcome identities/hashes. Returned values are copies; modifying them
  cannot revise the internal records. There is no durable restart/import guarantee.
- The empirical probability and persistence comparator use the same shared
  `_probabilities` operation as `tornredeye.availability_forecast.evaluate`. Training is the
  unsmoothed positive fraction of earlier origin-eligible records with eligible
  outcomes **already accounted for by issuance**, including a cold-start bootstrap
  outcome. This slice starts with no training labels and does not replay historical
  forecasts to populate training. A later adapter may need a separately reviewed
  historical-label bootstrap; no historical prediction would become a live record.
- `resolve()` processes matured targets chronologically, once. It selects only
  snapshots captured by each target and uses the existing source-time rules:
  age at most 300 seconds, cached receipts do not renew age, bounded carry over
  failed attempts is flagged, known contradictions block older fallback, and
  regressing source times cannot replace a newer state. Missing/stale/conflicting
  labels remain explicit unresolved outcomes, never zero. A target source timestamp
  must be after issuance. Later-delivered evidence cannot repair an unresolved
  record retrospectively. Outcomes become available for training when accounted
  for, not merely when the target clock passed.
- An unmatured record remains pending. Unlike the closed-file historical evaluator,
  the processing clock establishes that a prospective target has passed; resolution
  does not require a collection exactly at or after that instant. A previously
  captured, still-fresh report may carry through the target under the frozen rule.
- `report()` exports records/snapshots, pending and matured-but-unaccounted counts,
  unrecorded hourly slots between the first/last recorded slot, origin/target
  coverage, and common paired Brier scores. It cannot measure coverage outside that
  span or failures before the first record without a live invocation log. The
  empirical cold-start abstention is excluded from paired scores, but its resolved label can train later
  records. Later-known source contradictions are separate audit annotations; they
  never rewrite stored forecasts, outcomes, training or scores. Scores from this
  offline core are accounting diagnostics, not measured live forecast performance.

This preserves source-reported availability as a proxy. It does not identify exact
in-game stock, restock times, purchase success or independent statistical samples.

## Independent AWS consumer

`aws_shadow_forecast.py` retains the Issue #51 private Hawaii Shark Fin pilot,
60-minute horizon, unsmoothed expanding empirical mean, persistence comparator,
300-second source freshness, paired Brier scoring and immutable issuance protocol.
Issue #49's frozen historical evaluation remains separate and unchanged.

The private live pilot is activated, with unattended hourly execution verified;
see [dated deployment status](deployment.md#verified-operational-status). This is
distinct from the offline core's synthetic accounting records and the template's
disabled default. Activation does not establish predictive usefulness, calibration
or public availability. The broader [adaptive direction](roadmap.md#adaptive-forecasting-direction)
does not change this pilot's fixed experimental contract.

### Invocation and evidence boundary

The existing hourly minute-02 EventBridge rule is disabled by default. Each run
uses actual processing time and makes at most one decision in its UTC-hour slot;
missed slots are never backfilled. A five-minute conditional lease bounds one
240-second invocation. A reserved slot without a forecast remains failed.

Inputs list only retrieval-hour prefixes intersecting `[cutoff - 10 minutes, cutoff]`
under `evidence/live-v1/`, normally one or two hours. All pages must complete;
malformed/repeated pagination, unknown keys, changed objects, the 2,000-listed-object
limit or exhausted execution budget abort the decision. Snapshots record queried
prefixes, complete inventory status, listed counts and unknown collection coverage
between observations. An empty complete inventory means no eligible observation,
not an observed zero or proof that collection succeeded.

Partitioning does not prove availability. Conditional GET/read acknowledgements
bind exact bytes, ETag and S3 LastModified. LastModified plus one second must be
at or before the input cutoff; the boundary second and later writes are excluded.
Original retrieval time must match its partition and cannot follow persistence.
Source/export validity and freshness remain separately checked. Delayed writes
into queried hours are considered only if demonstrably persisted by the cutoff;
older retrievals cannot supply a fresh source under the existing validity rules.
Parseable partial transport failures cannot supply observations. Raw-only success
can be normalized from its preserved context; an unavailable raw counterpart
leaves a normalized attempt unknown.

Outcomes processed later query the target's fixed partitions and original S3
availability boundary, never today's evidence as if known then. Out-of-order
retrievals are sorted; simultaneous ambiguous retrievals fail. Cached observations
do not renew source freshness. Conflicts and missingness preserve existing method
semantics. Resolved outcomes are written/read back before contributing to a later
issuance. A scan crossing an hour boundary cannot create a backdated forecast.

### Storage and recovery

Immutable records use
`forecasts/live-v1/records/<epoch>/<YYYY-MM-DDTHH>/{forecast,ack,outcome,checkpoint}.json`.
Mutable head/lease use `forecast-state/live-v1/<epoch>/`. Forecast identities include
lineage and epoch. Checkpoints retain sufficient statistics, a predecessor/hash,
new resolution references and bounded pending slots, never cumulative histories.
Create-only forecasts and their readback acknowledgements establish genuine
pre-target persistence; late/ambiguous writes are excluded from training/scoring.
Same-hour retries preserve the original bytes and recover lost acknowledgements.

Azure backup covers all immutable records across epochs and their embedded input
provenance. Mutable head/lease are excluded. Ordinary restart uses existing state;
disaster recovery permits a new `ForecastEpoch` with cold training. Recovered old
forecasts and receipts remain audit evidence, never backdated new predictions.
No historical cohort or archive label is silently pooled into this training run.

### Validation, permissions and activation

Tests use fake AWS clients and clocks. The independent package includes shared
normalization, selection and accounting; it makes no YATA requests. Consumer IAM
allows only fresh evidence/hour and exact ledger/state discovery, evidence/epoch
reads, conditional immutable creates and conditional mutable state updates.
There is no bucket, queue, database or new trigger. Retain private evidence and
ledger objects; do not infer live retention from local templates.

Run `tools/package_shadow.py`, deterministic tests and `sam validate --lint` for
`shadow-template.json` offline. See [deployment](deployment.md) for ordered,
separately authorized preflight, deployment, smoke tests and activation. Runtime
reads are bounded to recent hours; source cadence is unchanged. Immutable forecast
storage grows by at most four records per recorded slot, with bounded per-record
snapshots and no lifetime-wide routine forecast inventories.

## Requested-time offline accounting

`RequestedForecastRun` in `tornredeye.requested_forecast` is a separate private,
in-memory requested-time operation. It reuses `OfflineShadowRun` evidence capture,
clock validation and retrospective audit annotations without changing that class,
the frozen historical evaluator, or the AWS pilot's hourly identities and records.
It has no provider access, storage adapter, scheduler, persistence/restart guarantee
or interface for importing historical forecasts as issued decisions.

```python
from datetime import datetime, timedelta, timezone
from tornredeye.requested_forecast import RequestedForecastRun

run = RequestedForecastRun(country="haw", item_id=1485,
                           maximum_source_age_seconds=300)
run.observe(verified_history)
forecast = run.issue(datetime.now(timezone.utc) + timedelta(minutes=10),
                     request_id="private-request-1")
# capture additional verified history as it becomes available
outcomes = run.resolve()
report = run.report()
```

`verified_history` is a caller-supplied, previously validated stock-history report;
optional `observe(..., attempts=...)` uses the existing normalized-attempt index.
The operation does not independently verify raw archives. Retain original private
history and evidence matching the snapshot's input digest; captured normalized
rows preserve evidence keys, original retrieval/source timestamps and issues.
The clock defaults to UTC now; inject fake clocks only for deterministic offline
exercises. Japan Xanax (`jap`/`206`) uses the same class with a separate run and
training history. The example horizon and freshness limit are caller choices,
not universal product settings.

- `observe` captures cumulative evidence at its processing-clock time. Earlier
  source/retrieval timestamps do not establish earlier downstream availability.
  Changed or removed captured rows and future retrievals fail. New raw evidence
  may replace a previously unknown normalized attempt only in a later snapshot.
  Selection binds the snapshot ID and capture time; `first_retrieved_at_utc` is
  original retrieval context, not a downstream availability assertion.
- `issue(target_utc, request_id=...)` accepts an aware datetime or ISO timestamp
  strictly after actual issuance. Multiple targets within one hour are allowed.
  A nonempty caller request ID may be issued once per run; duplicate IDs, including
  attempts to change their target, fail without rewriting the original record.
  Retrieve prior decisions from `report()`. Forecast hashes bind the offline
  record kind, selected pair, request ID, actual issuance/target, methods and
  freshness setting. These are run-local identities, not a new durable S3 contract.
- Only snapshots captured by issuance may supply inputs. Source age is measured
  at the decision, never renewed by cached retrievals. Failed/unavailable attempts
  can carry a previous valid report within the chosen freshness limit, explicitly
  flagged; they never create observed zero stock or hide a known source conflict.
  Missing/stale/conflicting origins have `origin_ineligible` status and null
  probabilities. An eligible origin without resolved training produces
  `no_resolved_training_labels` and a null empirical probability; its persistence
  comparator still reports the eligible origin's positive/zero state.
- The empirical probability is the unsmoothed positive fraction of earlier
  origin-eligible outcomes already accounted for by `resolve()`. Each target source
  timestamp contributes once per run despite repeated requests or cached reports.
  Eligible resolved outcomes are pooled across requested horizons; the current
  empirical probability does not yet condition on travel time, time of day or
  restock windows. Different target times can therefore receive the same
  probability when issued with identical information.
  No labels are fabricated from historical observations. `training` retains the
  contributing forecast/outcome hashes, source states and resolution times.
  Local `operation_sequence` values prove call ordering within this object: a
  completed resolution may train a later issuance at the same timestamp, whereas
  a matured but unaccounted target cannot. This is stronger offline accounting
  information than retrospective replay's timestamp-only maturity convention,
  but it is not an independently verified live persistence acknowledgement.
- `resolve()` accounts matured targets once in target-time order. Outcomes use
  only snapshots captured by each target; later delivery cannot repair a missing
  outcome. Targets require a source update after issuance. No such update, stale,
  missing or conflicting evidence remains explicitly unresolved, never negative
  training data. The processing clock establishes maturity; collection exactly at
  a target is unnecessary if a qualifying earlier captured report is still fresh.
  Returned records are copies. Later contradictions are audit annotations and do
  not rewrite stored forecasts, outcomes, training or paired scores.

Every forecast, outcome and report says `offline_accounting_only` and
`live_issuance_verified: false`. The report exports snapshots and private records,
paired Brier diagnostics, positive/zero outcomes, abstentions, pending targets and
matured-unaccounted counts. There is no assumed request schedule or inferred
missed-hour coverage. Source states and overlapping requests remain dependent;
a small eligible sample or a probability of zero/one is not evidence of calibrated
accuracy, uninterrupted in-game stock, exact restock timing or purchase success.

Before any live integration, separately authorize and review the arbitrary-request
durable identity/deduplication and write/readback attestation contract, recovery,
evidence availability, storage/backup scope and any permissions or automation.
Do not write these records into the Hawaii pilot's hourly ledger or change its
immutable record format, permissions or schedule. No cloud action is authorized
by this offline capability or its tests.

Focused validation: `python -B -m unittest discover -s tests -p test_requested_forecast.py`.
