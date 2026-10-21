# Private prospective shadow forecasts

Issue #51 includes `shadow_forecast.OfflineShadowRun`: a standard-library,
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
  `_probabilities` operation as `availability_forecast.evaluate`. Training is the
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

## Independent AWS consumer (implemented, not activated)

`aws_shadow_forecast.Consumer` reuses the normalizer, stock-history target
validation, baseline selection/probabilities and offline accounting identity,
outcome-label and paired-score operations. `shadow-template.json` defines a
separate stack with a private Lambda and an EventBridge scheduled rule at minute
02 of every UTC hour. Its state defaults to `DISABLED`. No collector stack,
evidence bucket configuration, polling control or source request changes are
needed. Invocation payloads cannot select a bucket, target, cutoff or issuance
clock. The pilot is haw/1485, with an actual issuance + 60 minute target.

### Invocation and evidence boundary

The consumer streams both existing canonical UUID prefixes using paginated LIST:
`raw/yata/jap/206/` and `normalized/yata/jap/206/`. It reads candidate objects only
within ten minutes of each decision cutoff. This intentionally accepts growing
**metadata listing cost**, rather than adding an event index with another delivery
and completeness contract. It does not load cumulative stock history or duplicate
raw exports. The full raw exports already include Hawaii; the existing Japan
normalized values never substitute for Hawaii values. Normalized records identify
failed/unavailable attempts. Corresponding raw/normalized retrieval times must
agree. A raw write without a later normalized write still supplies evidence.

For an origin, the input cutoff is fixed before its scan. Issuance uses the actual
clock after the scan, including freshness at that later instant; inputs that
arrive during the scan cannot enter this forecast. Scanning across an hour fails
instead of backdating a forecast. For an outcome, the cutoff is the original
target. A later invocation independently inventories that historical window;
eligible raw evidence stored before that target can resolve the outcome even
when this consumer had never read it before. Retrieval timestamps alone never
prove availability. Pre-target retrievals persisted after the target are excluded.

S3 LIST/GET consistency and the collector's existing immutable writes underpin
this boundary ([AWS consistency documentation](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html#ConsistencyModel)).
Each conditional GET binds the listed ETag; the input manifest retains exact
body SHA-256, original metadata, key, size, ETag, S3 LastModified and read request ID,
plus the normalized selection rows. A metadata/list/get failure aborts rather
than accepting an incomplete scan. This assumes canonical evidence remains
retained and unmodified, as required by the collector contract; privileged
historical deletions cannot be detected from a later listing.

S3 object times have second precision. Evidence is admitted only if
`LastModified + 1 second <= cutoff`; boundary-second keys are recorded as
exclusions. Forecast verification conservatively requires
`LastModified + 1 second < target`. This loses a small ambiguous boundary cohort
rather than claiming unsupported chronology. Conditional writes and ETags are
not protection against a privileged administrator replacing evidence.

Ten minutes exceeds the frozen five-minute freshness bound. An eligible source
cannot have been retrieved/persisted before this window without violating the
existing source-time checks. Unknown attempts and stale rows inside the window
retain their status and bounded carry flags. With no observation in this window,
`no_observation` explicitly means no observed state in the bounded input window;
it does not assert that the archive contains no older stale observations.
Repeated cached responses do not renew freshness. Conflicts known by the cutoff
block selection; later contradictions cannot revise frozen origin/outcome records.
Manifests allow separate later contradiction audits; the live consumer does not
scan later history or rewrite scores to perform those audits.

### Storage and recovery

All derived objects use the existing private encrypted evidence bucket under
`shadow/haw/1485/v1/`, outside canonical prefixes and public dashboard access:

- `YYYY-MM-DDTHH/forecast.json`: one immutable decision per UTC-hour slot,
  including cold-start/ineligible decisions, actual issuance/target, methods,
  probabilities, bounded input manifest and training checkpoint hash.
- `YYYY-MM-DDTHH/ack.json`: immutable readback acknowledgement binding the exact
  forecast body digest, ETag, S3 timestamp and request ID; only its
  `live_issuance_verified` flag attests durable pre-target storage.
- `YYYY-MM-DDTHH/outcome.json`: immutable label, target-time input manifest,
  resolution time, forecast digest and common paired Brier errors, or explicit
  missing/stale/conflicting status. Unverified forecast writes cannot train or score.
- `YYYY-MM-DDTHH/checkpoint.json`: immutable cumulative counts, paired Brier sums,
  at most two pending slots, newly incorporated outcome hashes/acknowledgement
  times and a link/hash to the previous checkpoint. It retains no cumulative
  arrays of evidence, forecasts or training labels.
- `head.json`: small ETag-conditional pointer to the committed checkpoint.
- `lease.json`: ETag-conditional five-minute invocation lease. Busy invocations
  log/return `skipped: lease_busy`. There is no unsafe early release. After a
  crash, retry after expiry. The Lambda timeout is 240 seconds and the consumer
  checks its deadline/runtime budget; do not increase that timeout beyond the
  lease. No reserved concurrency or quota increase is requested.

Ledger writes require `If-None-Match: *`; only the two exact mutable keys accept
`If-Match`. Successful readback compares exact bytes and SHA-256 metadata, rather
than treating a PUT call or an in-memory object as an acknowledgement
([AWS conditional-write contract](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)).
A lost write acknowledgement raises visibly. On restart, an existing immutable
record is retrieved, never replaced with a newly computed prediction. A later
readback can verify the original stored timestamp. A late or ambiguous forecast
is retained with `late_or_ambiguous_write` and excluded from training/scoring.

Each invocation first resolves matured pending decisions, writes and reads back
outcomes, then commits a checkpoint reserving the current slot, then issues the
forecast. Unmatured decisions remain pending. Durable outcomes train only after
resolution/readback and before subsequent issuance, including eligible cold-start
outcomes; there is no historical-label bootstrap. Identical clock timestamps
retain this explicit operation order. A checkpoint committed before a failed
forecast write reserves that slot; a retry in the same hour may issue at its
actual retry time, while later hours count an absent forecast as a failed slot.
Orphan checkpoints can be adopted only against their original predecessor.
Outcome writes before a crash are incorporated exactly once through the pointer.

The Lambda response and logs report counts for closed slots, failed reserved
slots, uninvoked intervening slots, pending slots, origin/forecast/outcome/issuance
statuses and common paired Brier means. Coverage begins at the first reservation;
there is no fabricated pre-start coverage. Current/pending decisions are counted
as closed only when incorporated. Failures before a reservation remain Lambda
errors/logs. Repeated invocations in an already recorded hour do not assimilate
new labels or change that hour's forecast. Schedule jitter can defer a target's
resolution until the following invocation; that label cannot train earlier.
Cold-start empirical abstention and unresolved labels remain outside paired
scores. These dependent single-source scores do not establish validation,
calibration or precise in-game event times. Issue #49 is unchanged.

### Permissions, retention and operational size

The generated role can LIST only the two canonical prefixes, GET those prefixes
and the private shadow namespace, create shadow objects conditionally, update
only `head.json`/`lease.json` conditionally, and write its own 14-day logs. It has
no provider credentials, canonical write/delete access, public publication,
backup access or permissions to change AWS configuration. EventBridge can invoke
only this consumer. Deployment additionally needs normal CloudFormation/SAM
artifact upload and IAM role creation/pass permissions.

At steady cadence: 24 nominal invocations/day (720 per 30 days), roughly five
derived PUTs/hour (forecast, acknowledgement, outcome, checkpoint, pointer) plus
a lease PUT, and usually two or three evidence scans/invocation. With `R` raw and
`N` normalized objects, each scan costs approximately
`ceil(R/1000) + ceil(N/1000)` LIST requests (at least one per prefix). Each window
normally GETs about ten raw exports and ten compact normalized records at the
existing one-minute cadence. Readbacks add a small fixed number of ledger GETs.
Only these recent rows and one LIST page are retained in memory. A 2,000-object
window guard fails visibly on unexpected input volume.

Thus compute has a four-minute ceiling per invocation, and listing work grows
linearly with archive size, despite bounded memory and bounded new records.
At 43,200 objects in each canonical prefix, one scan is about 88 LIST requests;
720 invocations with two/three scans would be about 127k/190k LIST requests using
that fixed archive size (actual growth varies). Candidate GET bytes depend on
export size. Forecast/outcome manifests normally imply tens of MB of new private
JSON per month, not copies of the growing archive. These are request/storage
estimates, not a quoted AWS bill or measured runtime. If listings outgrow the
budget, the invocation fails and leaves an honest coverage gap; a chronological
index would be a separately reviewed follow-up. No collector request or storage
retention setting changes.

Canonical S3/Azure backups continue unchanged. Existing backup prefixes do not
include the shadow ledger: it stays in the existing retained S3 bucket, without
a new Azure replication guarantee. Preserve its complete namespace, including
checkpoint ancestry, for independent export/recovery. Do not delete/reset its
mutable keys to start another cohort; review a new namespace instead.

### Local validation and separately authorized deployment

The following local checks do not activate AWS resources:

```powershell
. .\.chatgpt\powershell-helpers.ps1
$env:SAM_CLI_TELEMETRY = '0'
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath python -ArgumentList @('-B', 'tools/package_shadow.py')
    Invoke-NativeCommand -FilePath python -ArgumentList @('-B', '-m', 'unittest', 'discover', '-s', 'tests')
    Invoke-NativeCommand -FilePath sam -ArgumentList @('validate', '--lint', '--template-file', 'shadow-template.json', '--region', 'eu-north-1')
}
```

**Do not execute the steps below without separate authorization.** No deployment,
manual genuine forecast, rule activation or live AWS change was performed as part
of this implementation.

1. Review this storage/trigger/IAM contract, archive size and the existing bucket's
   retained/private/SSE-S3 configuration. Confirm canonical retention covers
   pending targets and that the new namespace is excluded from public roles.
   Select the existing collector evidence bucket; do not create another collector.
2. Repackage locally. Set the actual bucket name and deploy the independent stack
   with its rule disabled, reviewing the change set. No collector template update
   or event notification configuration is needed.

```powershell
$shadowEvidenceBucket = '<existing-evidence-bucket>'
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath python -ArgumentList @('-B', 'tools/package_shadow.py')
    Invoke-NativeCommand -FilePath sam -ArgumentList @('deploy', '--template-file', 'shadow-template.json', '--stack-name', 'tornredeye-shadow', '--region', 'eu-north-1', '--resolve-s3', '--capabilities', 'CAPABILITY_IAM', '--parameter-overrides', "EvidenceBucket=$shadowEvidenceBucket", 'ScheduleState=DISABLED', '--confirm-changeset')
    Invoke-NativeCommand -FilePath aws -ArgumentList @('cloudformation', 'describe-stacks', '--stack-name', 'tornredeye-shadow', '--region', 'eu-north-1', '--query', 'Stacks[0].Outputs')
}
```

3. With explicit manual-issuance authorization, copy `FunctionName`/`RuleName`
   from stack outputs into the variables below. Invoke once, inspect the response,
   exact forecast, acknowledgement and logs, then invoke in a later UTC-hour slot
   after the target (and after the five-minute lease). This writes genuine private
   decisions, including a possible cold-start abstention. Check the stored label,
   manifest, training checkpoint, missingness and paired coverage.

```powershell
$shadowFunction = '<FunctionName-output>'
$shadowRule = '<RuleName-output>'
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath aws -ArgumentList @('lambda', 'invoke', '--function-name', $shadowFunction, '--region', 'eu-north-1', '--cli-binary-format', 'raw-in-base64-out', '--payload', '{}', '.aws-sam/shadow-smoke.json')
    Get-Content -LiteralPath '.aws-sam/shadow-smoke.json'
}
```

4. Only after separate activation authorization, enable the rule through an
   approved stack update preserving the bucket parameter. Repackaging ensures
   the deployed artifact matches the reviewed code. Keep the collector untouched.

```powershell
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath sam -ArgumentList @('deploy', '--template-file', 'shadow-template.json', '--stack-name', 'tornredeye-shadow', '--region', 'eu-north-1', '--resolve-s3', '--capabilities', 'CAPABILITY_IAM', '--parameter-overrides', "EvidenceBucket=$shadowEvidenceBucket", 'ScheduleState=ENABLED', '--confirm-changeset')
}
```

5. Inspect Lambda Errors/Duration/Throttles and private ledger coverage over the
   first several targets. No new alert/topic/dashboard is installed. If stopping
   is authorized, use the same approved stack update with `ScheduleState=DISABLED`;
   preserve all S3 ledger objects. An already running invocation may finish.
