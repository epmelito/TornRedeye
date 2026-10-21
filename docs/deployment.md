# Deployment and operation

Issue #53's approved clean reset is implemented locally. Nothing here authorizes
AWS/Azure access, deployment, stopping collection or activating schedules. Existing
live collection, dashboard and backups have not been changed.

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

## Exact separately authorized cutover

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
