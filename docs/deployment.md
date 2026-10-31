# Deployment and operation

## Verified operational status

As of **2026-10-23**, the completed all-country operational activation is recorded
in [Issue #55](https://github.com/epmelito/TornRedeye/issues/55): collection is enabled,
and hourly private shadow forecasting is enabled with unattended execution verified.
Daily Azure backup is enabled; scheduled GitHub Actions
[run 39926391370](https://github.com/epmelito/TornRedeye/actions/runs/39926391370)
succeeded. Independent Azure recovery previously passed. These are dated operational
observations, not guarantees of continuing health, complete coverage or forecast
quality. That dated record does not verify the public dashboard publisher's
enabled state; later operator observations appear in the stock activation boundary.

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

Issue #75 reuses the existing dashboard publisher, packaging and private hosting
bucket. The local template now prepares exact-key stock permissions and an S3
invocation grant; it does **not** configure or activate bucket notifications.
No cloud operation or public delivery is authorized by this preparation.

The operator's starting observations for this integration report a deployed
operations publisher with October 22 code and its hourly rule **DISABLED**,
with no stock-specific IAM statements or
notifications targeting it, and no hosted stock snapshot/page/assets. The React
release was built and validated with 12 assembled files and 12 matching metadata
entries. These are supplied observations, not a new AWS inspection or proof that
local code is deployed. CloudFront FREE remains active with delivery disabled.

### Ordered stock integration procedure (separately authorized)

1. The owner has accepted proceeding with a free, non-monetized website and
   visible YATA attribution. Preserve those accepted conditions. Explicit
   third-party permission has not been independently verified.
   Obtain independent review and separate authority for code/IAM changes,
   one bootstrap invocation, S3 publication and,
   later, notification activation. The operator needs `s3:GetBucketNotification`
   and `s3:PutBucketNotification` on the exact evidence bucket in addition to
   existing deployment, invocation and hosting-object permissions; this work does
   not grant those operator permissions. Approve a bounded invocation/request budget.
   Capture the actual publisher code/package, role policy, Lambda resource policy,
   dashboard parameters, operations schedule, full evidence-bucket notification
   configuration and hosting object bodies/properties privately for recovery.
   Verify the evidence bucket and publisher are in the same region and account.
   Preserve `FreePlanCandidate=ENABLED`, `DistributionEnabled=false`, the existing
   WAF/policies/Function and operations `ScheduleState=DISABLED`. Do not enable
   or otherwise change schedules. Do not change the collector stack,
   its role, evidence, source requests or collection schedule.
2. Package with `python -B -m tools.package_dashboard`; the existing artifact
   contains `dashboard_publisher.py`, `stock_publisher.py` and shared evidence-path
   validation. Review the dashboard stack change set: deploy the refreshed code
   together with the three stock statements in `BoundedDashboardPublication` and
   `StockPublicationPermission`. The statements allow only GET and conditional PUT
   on `public/stock.json`, preserving the proven `data.json` pattern: create with
   `IfNoneMatch: *`, replace with `IfMatch`. Existing normalized-attempt GET scope
   suffices; no raw reads, LIST, evidence writes or additional service are added.
   The invocation grant permits only `s3.amazonaws.com`, the exact
   `EvidenceBucketName` ARN and `AWS::AccountId`. It cannot enforce an object-key
   filter; notification filtering and handler validation provide that boundary.
3. Verify deployed package bytes/code hash, environment, role policy and the scoped
   resource-policy statement before any stock invocation or notification change.
   Wait for the Lambda update to complete successfully; stack completion alone
   does not prove correct runtime behavior. Verify the operations path through
   offline regression tests and package inspection. The live hourly rule is
   DISABLED, so no ordinary scheduled publication can be observed without enabling
   it. If live operations verification is necessary, require a separately authorized
   invocation of the existing publisher, without new source calls or schedule changes.
   Leave stock notifications absent. In particular, do not send S3 test/events to
   the October 22 code: it predates the stock dispatch path.
4. **Bootstrap the missing snapshot before metadata planning.** Select one exact,
   genuine persisted canonical attempt, using its actual evidence bucket/key,
   retrieval identity and SHA-256 metadata. Do not create/copy an evidence object
   to trigger publication, synthesize stock, relabel old source times or scan an
   archive for this purpose. Under the separate invocation authority, call the
   updated existing handler synchronously with a replay envelope containing a
   **`Records` array with exactly one record**, whose fields are:
   `eventSource: aws:s3`, `eventName: ObjectCreated:Put`,
   `s3.bucket.name: <verified evidence bucket>` and
   `s3.object.key: <URL-encoded exact persisted attempt key>`.
   This is an operator replay, not evidence that S3 delivered an event. The handler
   validates the canonical path, digest and document identity before constructing
   allowlisted public fields. Inspect both invocation errors and returned payload;
   a transport-success status alone is insufficient. Require `updated: 1` for the
   first creation, then GET/HEAD `public/stock.json` and verify schema, sanitized
   fields, actual source/retrieval times, `ContentType: application/json` and
   `CacheControl: public,max-age=15`. Keep genuine stale/failed/partial states
   visible. If the PUT outcome is uncertain or an object already exists, read back
   and reconcile; never force an unconditional retry or delete a newer snapshot.
5. The [metadata planner](dashboard.md#deterministic-origin-metadata-delivery)
   requires **both** `public/data.json` and `public/stock.json` in its complete
   inventory. It deliberately fails while stock JSON is missing; do not remove
   that check or insert a preview fixture. After bootstrap, pause only public
   writers under separate authority, wait for in-flight work and capture fresh
   inventory. Keep the operations hourly rule DISABLED throughout this metadata
   window; do not enable it. The owner-approved release uploads only the seven
   stock-specific static files from the current 12-file assembly: `stock.html`
   and six allowlisted `stock-assets/` files (including font/license). The five
   assembled operations files are preview input, not deployment targets. Apply
   the conditional-write and readback contract only to stock static writes;
   retain older hashed assets and their reviewed release metadata. Neither
   manifest nor preview JSON is public content.

   Preserve all six existing operations objects unchanged: the five static files
   and `public/data.json`, including their bodies, metadata and current `app.js`
   Content-Type. Its reported mismatch is not a stock-release repair prerequisite.
   The planner records operations files and both JSON objects as `preserve` with
   no write request, even when existing metadata differs from the stock manifest.
   It does not recreate missing operations files. The separately published genuine
   `stock.json` must exist, but the static release must not upload or overwrite
   either JSON. Verify preserved object properties against the captured inventory,
   not the assembly's operations metadata. Keep `index.html` as the operations
   root; changing it to Stock remains a separate decision.
6. Only after package, bootstrap and metadata verification, separately authorize
   the notification merge below. Keep the operations hourly rule DISABLED.
   Notification registration may cause a test
   invocation; the updated handler accepts `s3:TestEvent` without publication.
   Observe the next ordinarily persisted matching attempt and verify stock-only
   publication receipts, metadata, ordering/failure behavior and runtime. Do not
   generate new YATA requests or assume old objects will replay automatically.
   Existing async retry count zero and event age 60 seconds remain unchanged;
   duplicate/out-of-order delivery and failed or expired events mean some reports
   may be missed until later publication. Logs and browser age/error cues remain
   visible; do not promise one publication per acquisition.
   Shared regional concurrency is still shared with the collector: review ordinary
   bursts/quota and cost before activation; no concurrency change is prepared.
7. Keep CloudFront disabled until its existing security exception, metadata and
   explicit delivery gates pass. Preserve the accepted free, non-monetized website
   and visible YATA attribution conditions above. Later authorized delivery must verify
   routes, MIME types, private origin, conditional/compressed caching and measured
   publication-to-display delay against the
   [cache contract](dashboard.md#cloudfront-free-candidate-compatibility).
   Association correctness alone does not resolve origin-error header differences.

### Evidence-bucket notification merge

Use one new entry in `LambdaFunctionConfigurations`, with a unique stable ID and
the freshly verified **existing** unqualified publisher ARN. This fragment is an
entry to merge, **not** a complete bucket notification replacement payload:

```json
{
  "Id": "<dashboard-stack>-stock-publication",
  "LambdaFunctionArn": "<verified-existing-publisher-arn>",
  "Events": ["s3:ObjectCreated:*"],
  "Filter": {
    "Key": {
      "FilterRules": [
        {"Name": "prefix", "Value": "evidence/live-v1/"},
        {"Name": "suffix", "Value": "/attempt.json"}
      ]
    }
  }
}
```

This includes only normalized attempt candidates, not raw bytes, forecasts or
control paths. S3 filters are not a canonical-path validator: the handler still
requires `evidence/live-v1/YYYY/MM/DD/HH/<32-lowercase-hex>/attempt.json` and matching
stored identity/digest. It publishes to the separate hosting bucket, so its PUTs
do not trigger this evidence-bucket route. No SAM S3 event, bucket import, custom
resource or collector-template update is needed.

Under later authorization, GET the **entire** current notification configuration.
Preserve every existing Lambda, SNS, SQS and EventBridge entry, including IDs,
filters and events, and review overlapping ObjectCreated filters and destination
validity before adding this entry. An overlapping route, duplicate/conflicting ID
or an unexpected existing target is a review blocker, not permission to remove it.
Serialize notification administration with the bucket owner: refresh immediately
before PUT and compare with the reviewed baseline; stop and re-merge if it changed.
There is no conditional ETag update for this API. Submit the full merged writable
configuration with the verified expected bucket owner and destination validation
**enabled**, then GET and compare the complete configuration. Do not submit just
this fragment, an empty configuration, or a response's `ResponseMetadata` wrapper.
A failed validation blocks the whole update; do not bypass it. An ambiguous PUT
requires readback before retry. Keep the exact reviewed before/after configurations
privately, but do not blindly restore an old whole-bucket snapshot over later edits.
See [S3 replacement and validation semantics](https://docs.aws.amazon.com/AmazonS3/latest/API/API_PutBucketNotificationConfiguration.html)
and [filter overlap constraints](https://docs.aws.amazon.com/AmazonS3/latest/userguide/notification-how-to-filtering.html).

### Stock integration rollback

Keep delivery disabled and preserve ACTIVE FREE and all compatible associations.
Remove **only** this stock route from a freshly captured full notification
configuration, preserving unrelated/current entries; validate and read back the
merged result. Stop operator replays and account for queued/in-flight async work
and ambiguous writes before removing stock permissions or restoring older code.
Disabling the hourly rule alone does not stop S3-triggered invocations. Retain the
last valid stock JSON and prior static assets; do not erase evidence or force old
JSON over newer publication. If stock static changes need reversal, restore only
the reviewed prior stock bodies/properties conditionally while public writers are
paused. The six operations objects remain untouched, including their metadata.

Only after the route is absent and pending work reconciled may a separately
approved dashboard rollback remove `StockPublicationPermission`, the stock-only
IAM statements or the refreshed code. Inspect CloudFormation rollback effects
before execution: bucket notifications live outside this stack, so stack rollback
will **not** remove the route. A failed code/permission update must leave the route
absent; if an active route survives a failed rollback, reconcile/remove that route
before further rollback. Preserve existing data.json permissions, operations code
and captured schedule state. Never roll back the collector or detach the WAF,
restore incompatible policies or enable pay-as-you-go/public delivery.

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
which remains outstanding before public delivery. The owner's accepted
[free, non-monetized website and YATA attribution decision](#stock-publication-activation-boundary)
is recorded above; it is not independently verified third-party permission.
The security exception proposal is not owner acceptance, and correct Function
association does not prove error-header
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
   keys, absent retained-release manifests or stock-write metadata mismatches remain
   blockers. Preserved operations metadata differences do not require repair.
4. Preserve the accepted free, non-monetized website and visible YATA attribution
   conditions, resolve the exact error-response security decision, then obtain
   explicit authorization for a **15-minute verification-only
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
