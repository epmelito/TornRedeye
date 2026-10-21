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

## Independent AWS consumer (offline implementation)

`aws_shadow_forecast.py` retains the Issue #51 private Hawaii Shark Fin pilot,
60-minute horizon, unsmoothed expanding empirical mean, persistence comparator,
300-second source freshness, paired Brier scoring and immutable issuance protocol.
Issue #49's frozen historical evaluation remains separate and unchanged.

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
