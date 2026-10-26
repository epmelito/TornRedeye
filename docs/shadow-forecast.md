# Private prospective shadow forecasts

Issue #51 includes `tornredeye.shadow_forecast.OfflineShadowRun`: a standard-library,
in-memory accounting core, tested only with synthetic clocks and observations.
It performs no provider reads, file writes, scheduling or deployment. Its records
always say `offline_accounting_only` / `live_issuance_verified: false`; they are
**not proof that a prediction was published before its target**. There is no CLI
for backdating historical forecasts or importing a retrospective replay as issued.
Issue #49 and its frozen hourly retrospective evaluation remain unchanged.

## Core contract

- Construct a run with explicit `country` and `item_id` keyword arguments for the
  selected pair. `OfflineShadowRun` retains `haw`/`1485` as compatibility defaults
  for the original experiment; they do not restrict supported selections or set
  product defaults. Production-clock semantics use UTC now; inject a clock only
  for deterministic offline exercises.
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
240-second invocation. If a forecast write never persisted, a same-hour retry
after lease expiry may still issue using its actual processing time. Once that
hour has passed, an absent reserved forecast is counted as failed, never backfilled.

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

The following example selects Hawaii Shark Fin; `country`, `item_id` and the
freshness limit are required caller choices for `RequestedForecastRun`.

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
exercises. Other selections, such as Japan Xanax (`jap`/`206`), use the same class
with a separate run and training history. The example horizon and freshness limit
are caller choices, not universal product settings.

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

## Append-only requested-time measurement integration

`RequestedMeasurement` in `tornredeye.requested_measurement` is an operator-invoked
storage adapter tested offline with injected fake S3 clients and clocks. There is
no CLI that creates an AWS client, Lambda entry point, deployment, schedule, IAM
change or change to the active Hawaii pilot. Its supported operations are
`issue(target_utc, request_id=...)`, `resolve()` and `report()`; it reuses the
consumer's immutable storage/evidence primitives, not its hourly `run()` protocol.
Passing fake storage proves software behavior, not actual prospective performance.

The following synthetic example selects Hawaii Shark Fin explicitly; the adapter
requires `country` and `item_id` for each selected pair.

```python
from tornredeye.requested_measurement import RequestedMeasurement

run = RequestedMeasurement(fake_s3, "synthetic-private-bucket",
                           epoch="requested-example", country="haw", item_id=1485,
                           maximum_source_age_seconds=300, clock=fake_clock)
forecast = run.issue(future_target_utc, request_id="example-request")
# advance the fake clock and add source objects as they become persisted
outcomes = run.resolve()
report = run.report()
```

Other country/item pairs, such as Japan Xanax (`jap`/`206`), use the same
implementation with independent training. An epoch can contain multiple pairs;
request identities include country/item and request ID.
Requested horizons share training within each item, with one contribution per
eligible target source timestamp. Methods/version IDs match `RequestedForecastRun`.
The unsmoothed empirical probability and persistence behavior are unchanged;
always-empty probability zero is only a comparator on the same scored outcomes.
Cold-start outcomes may seed later training even though the empirical forecast
abstained. No historical forecast/outcome import seeds training.

### Identity and chronological proof

The isolated epoch must match `requested-<name>` (at most 63 characters total).
Keys are
`forecasts/live-v1/records/<epoch>/requests/<identity-sha256>/{forecast,ack,outcome}.json`.
The hash binds country, item ID and the exact caller request ID, with the epoch
providing campaign isolation. Target time is deliberately not part of that key:
reusing the ID with a different target or freshness fails instead of silently
creating another prediction. Forecast hashes additionally bind actual issuance,
target, methods, probabilities, inputs and training provenance. Requests may have
multiple targets per hour, each with a distinct ID. No raw request ID enters the
storage path, and the active pilot's epoch/hour keys are never written.

Issuance happens after complete training reconstruction and evidence reads. The
record preserves actual decision time, requested target and actual horizon; scans
do not backdate issuance or move the requested target. Processing latency can make
the actual horizon differ from a caller's nominal research offset. Without a
committed request plan, reports retain exact actual-horizon groups.

For predeclared research cohorts, retain a private JSON list of entries containing
`country`, `item_id`, `target_utc` (canonical UTC with six fractional digits and
`Z`) and an arbitrary `horizon_cohort` label. Before issuance, derive each request
ID with `planned_request_id(plan, index)` and pass that ID to `issue()` with the
entry's target. The ID commits the complete plan's canonical JSON hash and entry
index in the existing immutable forecast record; no new persisted fields are
needed. Keep the exact plan for retries and reproducible reporting. Labels such
as `ten-minute` and `sixty-minute` describe research intent, not rounded elapsed
times or fixed product horizons. Do not select plan entries based on outcomes.

`report(request_plan=plan)` verifies these IDs and item/target associations and
returns `cohorts`, the plan and its SHA-256. Group keys are `cohort:<label>`;
legacy requests without a plan commitment remain visible under `unplanned`.
A different committed plan fails visibly rather than reassigning cohorts. Existing
uncommitted forecasts cannot retrospectively acquire a verified cohort. All
stored issuance, target and actual-horizon values remain unchanged in the audit
records. Cohort aggregation does not remove dependence from overlapping targets.
Freshness is required explicitly, currently supported from 0 through 300 seconds
within the existing ten-minute evidence discovery window. Use a new campaign for
a different freshness setting within an item; do not pool incompatible settings.

Every record is created conditionally and read back with matching bytes/digest.
The immutable acknowledgement retains the original forecast receipt and actual
readback time. Scoring requires both that readback and conservative S3 persistence
bounds for forecast and acknowledgement to precede the target. A payload's
`status: verified` alone is insufficient. A duplicate retrieves original bytes;
it does not recalculate probabilities. A lost forecast-write response can recover
before target. If the original acknowledgement persisted before target, its
receipt can be recovered after target. Without durable pre-target readback proof,
a later recovery remains ineligible, even if the forecast object was written
earlier. A never-persisted request cannot be reconstructed after its target.
Conflicting concurrent creates fail visibly; retry retrieves the winning record.

### Complete reconstruction without mutable state

Each operation lists every page under its campaign's request prefix. Only records
whose original S3 LastModified plus one second is at or before the operation's
cutoff enter that reconstruction. This freezes a conservative, complete input set
under the existing append-only primary-storage contract: later appends cannot
become eligible at that cutoff. There is no mutable checkpoint, lease or rolling
history truncation. It relies on primary S3 listing/read consistency and retained
immutable objects; overwrites, deletes and restoring into an active epoch violate
the contract. Conditional reads detect captured-object changes, not a privileged
writer's ability to forge historical provenance.

The boundary second is explicitly deferred, even when a preceding `resolve()`
completed at the same wall-clock reading. Its outcome becomes eligible at a
later cutoff after the conservative persistence bound, and must be successfully
read back before a later decision. This is an intentional storage-availability
constraint, not the in-memory object's operation-sequence proof. Missing outcomes
are never synthesized from matured targets during issuance. `resolve()` is the
separate accounting operation; persisted outcomes recovered by readback can train
later forecasts without double counting. Training retains outcome read receipts,
including actual read times, alongside source-time identities.

Limits are 3,000 listed ledger objects, 32 MiB total listed ledger bytes, 1 MiB per
ledger object, and the consumer's 240-second/remaining-budget checks. Oversized objects, unknown
keys, incomplete/repeated pagination, integrity failures or budget exhaustion
abort rather than training from a subset. These bound the initial measurement
campaign; routine work still grows with its full retained history. An exhausted
campaign requires a reviewed capacity/continuation decision, not silent rollover
or dropping pending outcomes. No lease/checkpoint alternative is implemented.
Raw source evidence has a separate `maximum_evidence_bytes` read budget per object,
defaulting to the existing incremental backup's 16 MiB operational limit. It can
be increased explicitly for larger valid responses; this is a resource bound,
not a YATA size-validity contract. Exceeding it aborts without truncating evidence
or issuing a forecast. The collector and active pilot are unchanged.

Outcomes are immutable and associated with exact forecast hashes. Accounting may
read evidence later, but only original source evidence demonstrably persisted in
S3 by the target supplies its label. This is S3-available source stock, **not**
proof of a consumer read at target. Late persistence cannot repair missing labels;
source age, post-issuance source updates, bounded carry, conflicts and failed
attempts retain their existing meanings. An unresolved outcome is final audit
evidence, not zero stock. Later discoveries do not rewrite forecasts or outcomes.

Reporting reconstructs recorded coverage by item and either committed research
cohort or exact actual horizon, including origin/forecast/outcome statuses,
cold-start abstentions, pending and
matured-unaccounted requests, issuance ineligibility, resolved positive/zero counts,
and matched empirical/persistence/always-empty Brier scores. It also returns the
stored records and inventory exclusions for private audit. Empty common samples
have null scores. Coverage is of persisted requests only: failures before a first
write and requests never submitted require a separately retained operator attempt
log; the adapter does not invent a denominator. Dependent source states and
overlapping outcomes do not establish independent validation, calibrated
probabilities, in-game availability or successful purchasing.

Offline checks: `test_requested_measurement.py` exercises fake-client issuance,
restart, late acknowledgements, training cutoffs and immutable outcomes. The
existing shadow/requested-core tests protect the active experiments. Backup key
validation and fake backup/restore tests cover the additive immutable layout.
See [backup scope](evidence-backup.md#scope-and-trust) and the separately authorized
[measurement activation boundary](deployment.md#requested-time-measurement-activation-boundary).
