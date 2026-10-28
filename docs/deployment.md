# Deployment and operation

## Verified operational status

As of **2026-10-23**, the completed all-country operational activation is recorded
in [Issue #55](https://github.com/epmelito/TornRedeye/issues/55): collection is enabled,
and hourly private shadow forecasting is enabled with unattended execution verified.
Daily Azure backup is enabled; scheduled GitHub Actions
[run 39926391370](https://github.com/epmelito/TornRedeye/actions/runs/39926391370)
succeeded. Independent Azure recovery previously passed. These are dated operational
observations, not guarantees of continuing health, complete coverage or forecast
quality. The public dashboard publisher's enabled state is **not verified** here.

This guide does not authorize further cloud access, deployment or schedule changes.

## Requested-time measurement activation boundary

Issue #68's append-only adapter is initially an offline, fake-client integration,
not an activated prospective experiment. Before any real client or source-storage
access is used, independently review the implementation and separately authorize:

1. The primary bucket, isolated `requested-<name>` campaign, item cohorts, requested
   targets, freshness, bounded invocation/accounting plan and private operator
   attempt log. Do not infer a permanent schedule or product horizon from research
   examples. Keep cold starts, late writes and unresolved targets in coverage.
2. Prefix-scoped List/Get on existing evidence and campaign records, and
   conditional-create-only Put on that campaign's forecast/ack/outcome keys.
   First inspect whether an existing operator identity already has the required
   scope; this implementation creates no role or permission. Do not broaden the
   Hawaii pilot's role, change its epoch or invoke its hourly handler for requests.
3. Deploy the additive backup key validator before writing new-format objects,
   preserve the existing backup scope/anchor pin, then verify actual backup and
   isolated recovery of a bounded sample. That later AWS/Azure work requires its
   own authorization; local fake clients establish no backup attestation.
4. Verify actual primary-store write/readback receipts before each target and
   immutable outcome readback before later training. Compare per-item/horizon
   paired scores only after genuine issued and subsequently accounted observations
   exist. A small sample, fake-client score or retrospective replay proves no live
   usefulness. Outcome evidence is S3-available by target, not necessarily read by
   the consumer then; it does not establish purchasing success.

Operational impact is additional S3 lists/conditional reads and up to three
immutable objects per persisted request, plus existing backup inventory/transfer
work. Full campaign reconstruction grows with retained records and fails at
explicit limits. No extra YATA requests or collector/polling changes are needed.
Estimate a bounded request/accounting budget before activation; exact costs and
existing permission coverage have not been measured by offline tests. Do not
introduce a service, schedule or mutable coordinator merely to run this adapter.

Rollback means stop operator invocations and any separately authorized automation;
retain immutable records and backup coverage. Keep the additive backup validator
while these records exist, since reverting it would reject their keys. Never
delete records to manufacture a clean restart, reuse an active/restored epoch, or
affect the independent Hawaii pilot. Account for interrupted/pending requests
explicitly before making a separate continuation/capacity decision.

## Stock publication activation boundary

Issue #75 adds local stock publication code and static assets, not production
wiring. Independently review these minimum changes before any cloud operation:

1. Verify YATA/Torn stock redistribution and attribution conditions. The page
   attributes both sources, but this does not establish permission to redistribute.
2. Package the existing dashboard publisher with `stock_publisher.py`; preserve
   the hourly operations path. Build and assemble the reviewed React stock frontend
   using the [local build contract](dashboard.md#build-and-local-player-preview).
   Upload the assembled `stock.html` and allowlisted `stock-assets/` files alongside
   the preserved operations assets; do not upload the retained legacy stock page,
   raw frontend source or a local preview snapshot. Keep `index.html` as the
   operations route;
   changing the distribution's default root to Stock is a separate product choice.
3. Extend the existing publisher role only with GET and conditional PUT on the
   exact hosting key `public/stock.json`, matching the existing `public/data.json`
   conditional-write policy. Its existing normalized-attempt GET scope suffices;
   no raw evidence, archive LIST, collector invocation or evidence writes are needed.
   The checked-in template still authorizes only the operations JSON key.
4. Route evidence-bucket ObjectCreated notifications, filtered to
   `evidence/live-v1/` and `/attempt.json`, to the existing publisher function.
   Review S3's exact SourceArn/SourceAccount Lambda invocation permission and
   preserve all existing bucket notifications. Do not add a publisher call to
   collector persistence or run hourly monitoring every minute. Notification and
   permission wiring is deliberately absent from the local templates pending review.
5. Confirm CloudFront's zero minimum TTL and stock response `max-age=15`, plus
   routes, content types, conditional publication, concurrent events and live
   publication-to-display delay. Do not infer deployed configuration from local
   templates. Existing asynchronous retry settings and delivery semantics need
   review: events may be duplicated/out of order, and a failed publication can be
   missed until a later event. Logs/errors and browser age/error notices remain
   visible; do not promise one publication for every saved acquisition.
6. Approve a bounded invocation/cost budget and inspect actual packaging,
   permissions and runtime before activation. Review notification bursts/backlog
   against the current shared regional concurrency quota so public reporting
   cannot starve collector execution. Code-path independence does not establish
   account-resource isolation; no concurrency or quota change is approved here.
   Use the stock-only operation; it
   performs no provider request and creates no new schedule, service or database.

Pause stock publication by removing only its separately approved notification
route; retain the last public snapshot so browsers show its age. Do not pause
collection or the operations schedule. Restore assets/function code only through
a separately approved deployment. No production or forecasting activation is
authorized by this local implementation.

## Offline validation

Use Python 3.11+; deployed functions retain Python 3.13 and the existing boto3 SDK.
From the repository root, capture commands with `.chatgpt/powershell-helpers.ps1`:

```powershell
. .\.chatgpt\powershell-helpers.ps1
$env:SAM_CLI_TELEMETRY = '0'
Invoke-CopyOutput {
    Invoke-NativeCommand python @('-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v')
    Invoke-NativeCommand python @('-B', 'tools/package_lambda.py')
    Invoke-NativeCommand python @('-B', 'tools/package_shadow.py')
    Invoke-NativeCommand python @('-B', 'tools/package_dashboard.py')
    Invoke-NativeCommand sam @('validate', '--lint', '--template-file', 'template.json', '--region', 'eu-north-1')
    Invoke-NativeCommand sam @('validate', '--lint', '--template-file', 'shadow-template.json', '--region', 'eu-north-1')
    Invoke-NativeCommand sam @('validate', '--lint', '--template-file', 'dashboard-template.json', '--region', 'eu-north-1')
}
```

Packages preserve deployed handler names and include the shared package. No new
service, provider request, model, database, queue or frontend is introduced.
The existing private retained bucket, dashboard distribution, alarms, source
schedule and independent consumer stack are reused. Templates default schedules
to disabled; deployed states must be inspected rather than inferred from defaults.

## Polling safeguards and recovery

The new country-neutral source-control key is `control/yata/polling.json`. Source
identity belongs here: the guard coordinates all YATA acquisitions, not one item.
There is no automatic legacy fallback, initializer or control-key migration.
Missing/invalid state fails closed before source access.

Preserve the existing 60-second minimum spacing, bounded spacing-only wait,
180-second attempt lease, conditional ETag updates, Retry-After/Date handling,
restriction halts and stale-attempt fail-closed behavior. Evidence-lineage reset
is never permission to clear cooldown, halt, spacing mode or an uncertain attempt.
Collector async retries remain disabled and the schedule remains one minute.

## Historical cutover procedure

The all-country cutover and activation are complete as recorded above. The ordered
procedure below preserves the approved transition safeguards and audit context;
it is not a pending task list or permission to repeat initialization against the
active lineage. It does not establish verification of every optional step, including
dashboard publication. Future changes require their own approved scope and preflight.

1. **Live preflight approval:** inspect actual identities, stack parameters,
   schedules, IAM, bucket encryption/lifecycle/retention, backup configuration,
   historical inventory/receipt coverage and intended costs. Confirm the new
   operational prefix/state roots are unused. Preserve the frozen archive code,
   configuration and independent pins. Approve archive retention arrangements;
   CloudFormation Retain alone is not sealing or protection against deletion.
2. **Maintenance approval:** stop old forecast issuance and collector scheduling,
   block manual/queued writers and drain in-flight invocations. Record UTC gap
   boundaries. Archive unresolved forecast targets honestly; do not manufacture
   their outcomes or carry their training state into the new cohort.
3. **Final archive/export approval:** perform complete paused inventories and the
   final verified Azure transfer/recovery checks with historical tooling. Record
   exact scope, counts, hashes, cutoff, failures and gaps. Stop the old backup job
   after completion. Keep original S3 keys, JSON, Azure archives/catalogs/pins and
   forecast records unchanged; remove operational writers' access to the archive.
4. **Polling-control cutover approval:** while all source writers remain stopped,
   read the old `control/yata/jap/206/polling.json` with its ETag. Validate it and
   confirm no active/uncertain attempt; stale attempts require operator investigation,
   never an automatic clear. Copy its exact body to `control/yata/polling.json`
   with create-only PUT; read back and compare, then re-read the old ETag to prove
   no change. Preserve revision, updated_at, not_before, halt_reason, attempt and
   spacing_only exactly. A different existing neutral control is a stop condition.
   Keep the old object for audit; never run old and new guards concurrently.
5. **Deployment/IAM approval:** review change sets and update the existing collector,
   shadow and publisher stacks in eu-north-1, preserving physical retained resources.
   Deploy all schedules disabled. Keep destination bucket and handler identities;
   use `ForecastEpoch=epoch-1` only for the fresh initial state. Review prefix-scoped
   conditional evidence writes/reads, exact raw-absence LIST, bounded consumer LIST,
   immutable forecast creates and conditional state updates. Collector cannot write
   old evidence; publisher only reads normalized attempts and publishes its existing
   allowlisted public object. No public raw evidence or new dashboard assets.
6. **Azure configuration/initialization approval:** configure new operational root
   permissions, `BACKUP_ANCHOR_SHA256` and AWS read scope for source evidence plus
   immutable forecast records. Run the guarded backup manually against the empty
   lineage; create/read back its anchor and verify empty coverage. Preserve old Azure
   artifacts and old pins privately; do not reuse the historical bootstrap setting.
7. **Source smoke-test approval:** invoke the collector once, through the preserved
   guard. This is a real YATA request, never an extra polling pipeline. Verify raw
   and canonical document, provenance, partial/failure behavior and CloudWatch
   heartbeat semantics. Invoke the existing publisher and check its public output.
   Transfer new evidence to Azure and prove isolated restoration.
8. **Activation approval:** enable normal one-minute collection and daily backup.
   Separately authorize a shadow invocation; inspect immutable forecast/readback
   acknowledgement, epoch, target and scoped input inventory, then backup/restore
   those records. Enable the existing hourly minute-02 rule only after verification.
   A later invocation verifies outcome resolution and training order; never backdate
   a missed hour to make the smoke test pass. Record deployment revisions, UTC start,
   monitoring evidence and gaps.

Each gate requires explicit authorization; approval of offline implementation does
not supply it. The workflow is main-branch guarded, so any eventual Git/GitHub
publication/configuration operations also require separate lifecycle authorization.
Use reviewed SAM change sets, existing stack names and freshly built artifacts;
these steps do not imply bucket replacement or automatic bootstrap state creation.

## Operator inspection and resumption

On uncertainty, disable source scheduling first and inspect the neutral control,
last response/restrictions and in-flight execution. Never clear a halt or shorten
not_before solely to resume collection. Any justified control amendment requires
operator authorization, an exact reviewed body and ETag-conditional write, with
writers stopped. A future neutral-key cutover cannot initialize a fresh permissive
control while an old cooldown/halt exists.

## Crash recovery limits

Ordinary restarts resume the existing verified forecast epoch/checkpoint protocol.
S3 raw-only writes remain evidence and identical persistence retries complete the
missing document. Invocation redelivery is a new guarded acquisition, not replay
of old evidence. Backup interruptions resume through immutable intents/receipts.

Disaster recovery instead restores independently verified data into an isolated
private destination, retains original receipt times for audit and starts a new
explicit forecast epoch with cold training. Never restore mutable head/lease or
pretend restored S3 timestamps are original availability. See [backup recovery](evidence-backup.md).

Rollback stops new writers and schedules, preserves both datasets and records the
gap. Do not point new readers at historical prefixes, reuse a forecast epoch with
restored active state, or silently reopen the sealed historical writer. Such an
operational reversal requires separate review and authorization.
