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
5. Verify the FREE managed cache policy's one-second minimum and the explicit
   stock response `max-age=15` against the
   [cache contract](dashboard.md#cloudfront-free-candidate-compatibility), plus
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

## CloudFront Free activation boundary

### Verified hosting state and ownership

**Operator-verified state supplied 2026-10-31**, not a new AWS inspection by this
local implementation, in workload account `940521993273`:

- Exactly one matching CloudFront **FREE** subscription is **ACTIVE**, created
  through the AWS console, outside CloudFormation, and includes the existing
  distribution and WAF. No scheduled subscription change was reported.
- Distribution `E30SMD8RRAENEI` is **Deployed and disabled**, with live
  `PriceClass_All`. The dashboard stack has `FreePlanCandidate=ENABLED` and
  `DistributionEnabled=false`.
- The existing WAF association, managed cache policy, managed response-header
  policy and viewer-response Function remain correct. The WAF byte-match rule
  was successfully removed. The ACL has only `SourceIpRate`, in COUNT at 3000
  requests per IP over 300 seconds, with default ALLOW.

The subscription is console-owned. No local runtime or recovery code depends on
its former uncommitted creation template, which has been removed along with its
exclusive test. **Do not create or import another subscription.** A dashboard
stack update, rollback or deletion does not manage this subscription's lifecycle.
Issue #75 remains open; this reconciliation authorizes no AWS operation.

The dashboard stack continues to own the distribution and its associations.
`dashboard-template.json` now selects `PriceClass_All` through the existing
`UseFreePlanCandidate` condition; noncandidate mode preserves the historical
`PriceClass_100`. The managed policy IDs and Function association are unchanged.
Custom cache/security policy resources remain in the template but are unattached
in FREE mode. They are historical compatibility resources, **not an authorized
rollback target while FREE is active**.

`cloudfront-waf-template.json` still describes only the dedicated CLOUDFRONT ACL
in `us-east-1`, default ALLOW, with the native source-IP rule defaulting to COUNT
at 3000 requests per 300 seconds. Its creation default remains disabled. The
existing ACL was confirmed to belong to the deployed `tornredeye-cloudfront-waf`
stack in `us-east-1`; its successful update and current single COUNT rate rule
were independently verified. Do not create a second ACL or infer deployed state
from template defaults; require fresh verification before future operations.

### Historical context and remaining gates

The **2026-10-29** operator baseline had enabled delivery, no WAF/Functions and
an SCP denial of CreateSubscription. Those dated observations do not describe
the current verified hosting state or establish current IAM/SCP permissions.
On **2026-10-30**, the operator reported the console rejection "configuration not
available in this tier: byte match". The local UnexpectedPublicPath proposal and
its exclusive controls were removed. That lost COUNT-only path diagnostics,
not enforced blocking. Successful live removal is now part of the October 31
verification. No replacement Function, service or path filter is proposed.

An ACTIVE plan does **not** resolve the
[error-response security incompatibility](dashboard.md#error-security-comparison-and-proposed-exception)
or the [source redistribution/attribution gate](#stock-publication-activation-boundary).
Both remain outstanding before public delivery. The exception proposal is not
owner acceptance, and correct Function association does not prove error-header
equivalence. Preserve private S3/OAC, `/public`, the operations `index.html` root,
collector independence and existing public publication contracts.

COUNT observes rate matches; it does not block requests or reduce S3 misses.
Random paths and real objects requested across edges/variants still expose
variable origin request costs. Per-IP BLOCK remains a separate decision requiring
an approved NAT/mobile/multiple-tab baseline and threshold plus WAF-error security
acceptance. Distributed traffic can bypass a per-IP bound. Schema lint does not
prove plan acceptance, as the earlier byte-match rejection demonstrated.

### Further verification and delivery require separate authorization

There is no prospective subscription-creation or WAF-provisioning step for the
current state. No CreateSubscription permission expansion, subscription import,
paid tier, cancellation or account upgrade is part of this reconciliation.
Later cloud reads/writes need their own authorization and effective permissions.

1. Keep the existing distribution disabled and retain ACTIVE FREE coverage and
   the associated ACL. Under later read authorization, capture current templates,
   physical resource IDs, subscription identity/resources/status and any scheduled
   change, distribution config/ETag, parameters, schedules and public metadata.
   The ACTIVE FREE subscription ARN was supplied in the operator's earlier AWS
   CLI evidence. Verify the current identity and state afresh before future
   operations rather than hardcoding mutable IDs or relying on this snapshot forever.
2. Review any eventual dashboard update against the existing stack and preserve
   actual parameters: `FreePlanCandidate=ENABLED`, `DistributionEnabled=false`,
   the current `WebAclArn`, and the real schedule state. Template defaults are
   not deployment instructions. FREE mode selects PriceClass_All; never apply
   historical PriceClass_100 or incompatible custom policies to this active plan.
   The live PriceClass already matches the corrected branch of the local template.
3. Complete the [metadata dry-run and readback contract](dashboard.md#deterministic-origin-metadata-delivery)
   for existing/new public objects and retained assets, with public writers paused
   only under separate authority. Do not modify collection, add source requests,
   enable notification wiring or publish synthetic evidence. Missing JSON, unknown
   keys, absent retained-release manifests or metadata mismatches remain blockers.
4. Resolve redistribution/attribution and the exact error-response security
   decision, then obtain explicit authorization for a **15-minute verification-only
   delivery window**. Reconfirm ACTIVE FREE, the associated ACL, compatible policies,
   Function and deployed configuration before any enablement. Keep COUNT initially.
   Verify operations/stock routes, GET/HEAD, conditional/compressed responses,
   cold/warm cache headers, private-origin denial and safely observable error cases.
   Record status, headers, Content-Type and sanitized error bodies; do not inject
   faults into collector/evidence services. Missing safe 5xx evidence stays explicit.
5. Measure publication-to-display delay using genuine, separately authorized
   publication receipts without new YATA calls. Degraded/frozen source evidence
   does not prove new source freshness. Do not substitute a synthetic snapshot.
   At the end of the window, disable delivery again unless required evidence and
   explicit final owner acceptance authorize continued public operation.
6. Review actual native metrics and an approved request budget before any separate
   rate BLOCK change. Do not flood production merely to exceed 3000 requests.
   If verification fails, keep delivery disabled with the ACTIVE subscription and
   WAF association intact. Never silently restore pay-as-you-go public delivery.

### Failure and rollback boundary

- The verified safe holding state is **disabled delivery with ACTIVE FREE and
  its existing WAF association**. Preserve the managed policies, Function and
  PriceClass_All. Reverting `FreePlanCandidate` to DISABLED, restoring incompatible
  custom policies/PriceClass_100, clearing WebAclArn or detaching/deleting the ACL
  while FREE is active is not an authorized rollback.
- A future approved update may roll back only to a captured, FREE-compatible
  disabled configuration. Inspect change-set and rollback effects on enabled
  state, associations and parameters before execution. If rollback restores
  delivery or incompatible settings, stop and escalate to the owner for explicit
  recovery authority. Do not use older pre-FREE defaults as a recovery shortcut.
- Stack success/failure is not subscription lifecycle evidence. The existing
  console-created subscription is outside CloudFormation and must not be
  recreated, imported or assumed cancelled because a stack rolled back.
- On ambiguous operations or failed rollback, keep delivery disabled, preserve
  coverage, capture exact resource/event state and reconcile ownership under
  recovery authority. Do not launch replacement resources, blindly skip failed
  resources or delete an ambiguously owned ACL. No automatic cleanup timer exists.
- Cancellation or a return to pay-as-you-go would require a separately approved
  transition with effective cancellation/recovery authority, verified disabled
  delivery, actual cancellation-state checks, origin-cost acceptance and explicit
  ACL cleanup ownership. This procedure does **not** authorize that transition.
  Disabling delivery alone does not cancel a plan or eliminate all AWS charges.

No step changes collection, forecasting, evidence retention, schedules or public
publication permissions. The former pre-subscription 30-minute WAF bridge window
is historical planning, not a current activation step or hard cost cap. See
[remaining costs](aws-cost-protection.md#target-compatibility-and-security-gates).

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
