# Historical evidence export and local verification

[Issue #19](https://github.com/epmelito/TornRedeye/issues/19) provides offline-tested
tooling in `evidence_backup.py`. Passing synthetic tests does **not** establish
that a live backup exists. No restoration or recurring backup is implemented.
The [evidence continuity requirements](aws-cost-protection.md#evidence-continuity-and-recovery-requirements)
remain authoritative for storage ownership, independent copies and recovery.

## Approval and preparation

Live inventory/cost estimation, full export, collector cutoff, encrypted external
storage/key ownership, two-copy verification and isolated restoration each require
separate approval. The CLI requires `--live-read-approved` for inventory/export;
this flag records operator intent and does not grant approval or AWS permissions.
Do not run those operations as part of offline implementation or review.

Use Python 3.11+ locally. Offline verification and tests use only the standard
library. Separately approved live reads require an existing boto3 installation
and owner-selected credentials through its normal credential chain. The client
uses `eu-north-1`, 10-second connection/30-second read timeouts and at most three
SDK attempts per request. It does not modify IAM or resources.

For full export, required read permissions are `s3:ListBucket` on the private
evidence bucket, restricted by `s3:prefix` to exactly `raw/yata/jap/206/` and
`normalized/yata/jap/206/`, and `s3:GetObject` on objects under those prefixes.
No PutObject, DeleteObject, polling-control, logs or deployment-artifact access
is needed. The repository configures SSE-S3; verify deployed encryption before
export. KMS encryption would require a separately reviewed decrypt permission.
This inventories current
objects, not historical versions or evidence already deleted.

Choose an owner-only backup directory **outside all Git working trees**, on a
filesystem supporting atomic rename and hard links (such as NTFS). The tool
rejects Git worktree destinations and existing symlinks/junctions. Ignore rules
also cover conventional local backup names. Filesystem access and encryption
must protect this directory: exact payloads, metadata, keys and manifests are
private. The tool does not encrypt or synchronize them.

For consistent inventory, separately approve pausing every writer, including
manual collector invocations, and confirm queued/in-flight writes have drained.
Record the UTC pause/gap boundaries and a cutoff timestamp after draining.
Keep writers paused and prohibit object mutation/deletion through inventory,
export and any reruns. `--paused-cutoff-utc` is an operator attestation of this
condition; the tool cannot verify the pause. A missing attestation always prevents
a consistent-backup success exit, even when all returned pages/files verify.

If collection continued, the pause ended, or the attestation is doubtful, start
a new inventory in a new directory after an approved stable cutoff. Do not edit
the old manifest to assert completeness. Concurrent-write reconciliation is not
implemented. Preserve the old evidence and record the limitation.

## Inventory and cost preflight before export approval

[Issue #21](https://github.com/epmelito/TornRedeye/issues/21) prepares the following
future procedure. Only documentation and public pricing research are authorized
for this package. No account inspection or inventory has been executed.

### Read-only prerequisites

Before separately approved live execution, confirm the owner-supplied bucket,
account ID, `eu-north-1` region and credential identity. Inventory needs only
[`s3:ListBucket`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_ListObjectsV2.html)
on `arn:aws:s3:::<evidence-bucket>`, with `StringEquals` on `s3:prefix` allowing
exactly `raw/yata/jap/206/` and `normalized/yata/jap/206/`. An
[IAM numeric condition](https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_elements_condition_operators.html)
`NumericLessThanEquals` on `s3:max-keys` of 100 can enforce the page size. Use an
existing identity with this approved scope; do not grant permissions in this
package. Inventory needs no `s3:GetObject`, writes or collector permissions.

With separately approved bucket-configuration reads, obtain the **live** lifecycle
configuration using `s3:GetLifecycleConfiguration` on that bucket. From the
repository root, using the existing helpers and AWS CLI, the future command is:

```powershell
. .\.chatgpt\powershell-helpers.ps1
$sourceBucket = '<approved-evidence-bucket>'
$sourceOwner = '<approved-12-digit-account-id>'
$env:AWS_RETRY_MODE = 'standard'
$env:AWS_MAX_ATTEMPTS = '3'
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath aws -ArgumentList @(
        's3api', 'get-bucket-lifecycle-configuration', '--bucket', $sourceBucket,
        '--expected-bucket-owner', $sourceOwner, '--region', 'eu-north-1',
        '--cli-connect-timeout', '10', '--cli-read-timeout', '30', '--no-cli-pager'
    )
}
```

Keep configuration output private. Review enabled rules, whole-bucket/prefix/tag
filters, expiration dates/ages, transitions and noncurrent-version expiration
against both prefixes. The exact
[`NoSuchLifecycleConfiguration`](https://docs.aws.amazon.com/AmazonS3/latest/API/API_GetBucketLifecycleConfiguration.html)
error means no lifecycle configuration; AccessDenied, other errors or unavailable
results mean **unknown**, not no expiration. Record UTC inspection time and
private results. Confirm deployed bucket ownership/region and, if retention
assurances rely on them, versioning/Object Lock using separately permitted
`s3:GetBucketVersioning`/`s3:GetBucketObjectLockConfiguration` reads. Repository
`Retain` and lifecycle templates do not prove deployed state or protection from
account closure/manual deletion. Expiration removal cannot recover prior losses.
Unexpected expiration/transitions require an owner decision; do not change them.

### Bounded provisional listing

Keep collection running for this provisional preflight and omit a cutoff
attestation. Use a fresh private directory outside Git. The CLI has a soft
runtime limit but no LIST-count cap; `--max-bytes` bounds export, not inventory.
This inline invocation reuses `inventory()` with a listing-only request limiter;
no new repository tool or SDK/persistence behavior is required. The example
proposes **100 logical LIST calls, 100 keys/page, 60 seconds and three SDK attempts
per call**; approve these limits and a total preflight spending allowance first.

After separate live approval, run from the repository root:

```powershell
$preflightDirectory = 'D:\PrivateEvidence\yata-provisional'
$inventoryScript = @'
import json, sys
from evidence_backup import inventory
if sys.argv[4] != '--live-read-approved':
    raise SystemExit('Separate live inventory approval is required')
import boto3
from botocore.config import Config
class RequestLimitReached(Exception):
    pass
class ListingOnly:
    def __init__(self):
        self.calls, self.classes = 0, set()
        self.client = boto3.client('s3', region_name='eu-north-1', config=Config(
            connect_timeout=10, read_timeout=30,
            retries={'mode': 'standard', 'total_max_attempts': 3}))
    def list_objects_v2(self, **request):
        if self.calls >= 100:
            raise RequestLimitReached()
        self.calls += 1
        page = self.client.list_objects_v2(**request, ExpectedBucketOwner=sys.argv[3])
        self.classes.update(obj.get('StorageClass', 'UNKNOWN') for obj in page.get('Contents', []))
        return page
client = ListingOnly()
manifest = inventory(s3=client, bucket=sys.argv[1], directory=sys.argv[2],
                     page_size=100, max_seconds=60)
complete = all(state['complete'] and not state['error'] for state in manifest['inventory'])
print(json.dumps({'logical_list_calls_this_run': client.calls,
                  'storage_classes_seen_this_run': sorted(client.classes),
                  'pagination_complete': complete, 'consistent_cutoff': False,
                  'prefixes': {prefix: {field: state[field] for field in
                      ('objects', 'bytes', 'pages', 'complete', 'error')}
                      for prefix, state in zip(manifest['prefixes'], manifest['inventory'])}}))
raise SystemExit(0 if complete else 2)
'@
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath python -ArgumentList @(
        '-B', '-c', $inventoryScript, $sourceBucket, $preflightDirectory,
        $sourceOwner, '--live-read-approved'
    )
}
```

The limiter forwards at most 100 calls, including failed calls. With the confirmed
region and [configured retry policy](https://docs.aws.amazon.com/boto3/latest/guide/retries.html),
budget up to 300 ordinary LIST attempts: **$0.0015 per invocation** at the rate
below. This request-component bound excludes credential-service calls, unexpected
region redirects and configuration reads; it is not a total-cost cap. Stop and
resolve unexpected region/identity behavior before another invocation. SDK
retries/timeouts, local checkpoint checks and credential resolution can exceed
the soft runtime. Do not
use console browsing, per-object HEAD/GET, debug logging or automatic rerun loops.
Budget configuration reads and listing-response egress separately; cap the
approved number of invocations and stop for review before replenishing it.

Each accepted page is checkpointed before the next listing. The summary reports
committed counts/bytes/pages per prefix, failures and classes observed this run;
it exposes no object keys/tokens. Capture each run privately. Storage class is
returned by S3 but **not saved in the existing manifest**; class observations may
include an uncommitted/repeated page, so do not sum them as object counts. Preserve
the observations from every slice; UNKNOWN, missing slice records or changing
classes leave retrieval assumptions unresolved. The manifest counts remain the
authoritative checkpointed listing totals.

Exit 2/helper failure means incomplete pagination, including `RequestLimitReached`,
`runtime_limit`, source errors or page conflicts. Counts from an incomplete
prefix describe only committed pages; do not extrapolate a full archive or treat
unknown coverage as zero. After reviewing the failure and remaining approval
budget, rerun the identical command/directory to resume its saved token. A killed
process can leave a lock; follow the interruption procedure below. Do not edit
tokens or delete conflicting pages. An exception before the summary is produced
also leaves the result unresolved.

Exit 0 here means both listings reached their final pages, **not** a consistent
cutoff or a verified backup. Under active collection, additions/deletions can
escape a traversal. For later full export, separately approve the pause/drain
requirements above and create a new stable-cutoff inventory; do not relabel this
provisional manifest. The existing CLI below intentionally uses backup-verification
exit semantics (normally 1 for inventory with pending files), unlike this
listing-only summary's 0/2 semantics.

### Pricing evidence and worksheet

Public prices checked **2026-10-11**, USD gross before tax, credits, allowances or
discounts. Region is EU (Stockholm), `eu-north-1`. Recheck before live approval.
The [S3 regional offer](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-north-1/index.json)
was published **2026-09-28**, with these terms effective **2026-09-01**.
The [data-transfer regional offer](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AWSDataTransfer/current/eu-north-1/index.json)
was published **2026-09-16**, with outbound terms effective **2026-06-01**.

| Component | Reference rate | Assumption |
| --- | --- | --- |
| LIST | $0.005/1,000 requests | Standard Tier1 rate; LIST uses it across storage classes |
| Standard GET | $0.0004/1,000 requests | All exported objects confirmed Standard |
| Standard-IA / One Zone-IA GET | $0.001/1,000 requests | Substitute for objects in those classes |
| Standard-IA / One Zone-IA retrieval | $0.01/GB retrieved | Additional to GET and transfer |
| Stockholm internet transfer | $0.09/GB, first 10,240 chargeable GB tier | Ordinary regional endpoint; account-wide tier/allowance use unknown |
| Existing Standard storage | $0.023/GB-month, first 51,200 GB tier | Ongoing source retention, not an extra copy or export fee |

[S3 pricing](https://aws.amazon.com/s3/pricing/) specifies LIST's Standard request
rate, no Standard retrieval fee, class-dependent retrieval/restore charges and
binary storage GB (`2^30` bytes). Glacier/archived tiers need a separately priced
restore plan; this inventory does not authorize RestoreObject. Use bytes as the
base unit; report local GiB as `bytes / 2^30`. For outbound estimates use the same
conversion as a planning assumption and reconcile AWS's billing-meter units.
[AWS's transfer FAQ](https://aws.amazon.com/about-aws/global-infrastructure/global-network/faqs/)
describes 100 GB/month free internet transfer shared across services/regions
(excluding China/GovCloud); unused eligible allowance is unmeasured. Assume zero
available allowance for the conservative estimate, not zero charges. CloudFront
allowances and credits do not establish free export.

Fill the worksheet only after approved measurements; no archive totals are known
here. Let `N`, `B` and `P` be summed checkpointed objects, bytes and pages for both
prefixes. For completed pagination at page size 100, minimum page demand is
`sum(max(1, ceil(N_prefix / 100)))`; use observed pages because S3 can return fewer
keys. Page totals exclude failed/retried/uncheckpointed calls.

- Inventory: `C_LIST = A_LIST * 0.005 / 1000`, where `A_LIST` is billable attempts
  across all approved invocations. Under the confirmed-region assumptions above,
  budget `3 * sum(logical_list_calls_this_run)`, at most `300 * invocation_count`.
  Add configuration-request charges and listing-response transfer separately;
  inventory uses **zero canonical-object GETs** and transfers no canonical bodies.
- Full export: minimum logical GET demand is `N` for a new destination;
  `C_GET = sum(A_GET_class * rate_class / 1000)`. Include SDK retries and restarted
  transfers; confirmed existing verified files need no repeat GET. Failed requests
  can be billable; consult [S3 error billing](https://docs.aws.amazon.com/AmazonS3/latest/userguide/ErrorCodeBilling.html).
- Retrieval: `C_retrieval = sum(retrieved_GB_class * retrieval_rate_class)`;
  Standard is zero for this component only. Unknown classes block a firm estimate.
- Transfer: `G_chargeable = max(0, G_sent - verified_remaining_allowance)` and,
  within the first tier, `C_DTO = G_chargeable * 0.09`. Include listing/configuration
  responses, repeated/partial transfers and transport overhead; billed sent bytes
  can exceed received bytes on interruption. Above that tier, price incremental
  bytes in the account's applicable tiers; the first-tier formula is not universal.
- Local capacity per full copy: `B + manifest/receipt bytes + filesystem overhead
  + largest-object staging headroom`. Inventory-only requires metadata space, not
  capacity for downloaded bodies. Verify free space before each slice/export.
- Two independent external copies: obtain owner-approved destination quotes and
  units; `C_external = sum(setup_or_media_cost + stored_units * monthly_rate * months
  + upload/request/egress/recovery_fees)` across both copies. Include encryption/key
  recovery costs. A local second copy does not require another AWS download; a
  second independent download repeats GET/retrieval/transfer costs. No destination
  quote, recurring cost or source-account-independent storage has been verified.

### Go/no-go for subsequent work

**Current recommendation: no-go for full live export.** This package establishes
no live counts, retention state, remaining allowances or destination quote.
Independent review of this procedure remains an acceptance gate. After review,
an owner may separately approve the bounded provisional listing and configuration
checks with a cumulative request/spending allowance. No action here changes IAM,
the collector, resources, account plan or canonical evidence.

Recommend full export only after complete, validated provisional page accounting,
resolved live retention/storage-class risks, a refreshed consistent-cutoff plan,
an owner-approved gross-cost estimate including retry/egress uncertainty, adequate
private capacity and approved encrypted independent destinations/key ownership.
If the owner needs an enforceable total-dollar cap, the request limiter/runtime
settings are insufficient: stop pending an approved cost-control decision.
Full export, two-copy verification and isolated restoration remain separate gates.

## Intended local workflow after separate authorization

The following commands are templates, not evidence of execution. Replace the
source bucket, external directory and UTC cutoff with approved values. The first
operation lists only; it does not download canonical bytes. Review private
per-prefix object/byte counts in `manifest.json` before approving full export.

```powershell
. .\.chatgpt\powershell-helpers.ps1
$backupDirectory = 'D:\PrivateEvidence\yata-cutoff'
$sourceBucket = '<approved-evidence-bucket>'
$cutoffUtc = '<approved-drained-cutoff-in-UTC>'
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath python -ArgumentList @(
        '-B', 'evidence_backup.py', 'inventory', $backupDirectory,
        '--bucket', $sourceBucket, '--paused-cutoff-utc', $cutoffUtc,
        '--max-seconds', '3600', '--live-read-approved'
    )
}
```

Inventory-only normally exits 1 because transfers remain pending; the command
helper reports that exit. Inspect the private manifest's page completion/error
fields to distinguish completed listing from a failed or interrupted listing.
Run the same command to resume listing at its checkpointed token. Listing uses
100 objects/page by default (the Python API permits 1–1000); it preserves every
listed canonical object regardless of status, filename, body or pairing.

Estimate disk use as the sum of inventoried bytes, plus JSON inventory/receipts,
filesystem allocation overhead for many small files, and staging space for the
largest object. Transfers restart an interrupted object from its beginning.
Estimate initial LIST requests from page counts and GETs from pending objects;
SDK retries, failed transfers and reruns add requests/egress. Check current
regional pricing, storage classes, retrieval fees and account-wide transfer
allowances before export. CloudFront allowances do not establish free S3 export.
No price lookup, cost ceiling, storage purchase or live inventory is performed
by offline preparation.

After full export approval, retain the pause and run:

```powershell
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath python -ArgumentList @(
        '-B', 'evidence_backup.py', 'export', $backupDirectory,
        '--max-bytes', '1073741824', '--max-seconds', '3600',
        '--bytes-per-second', '8388608', '--live-read-approved'
    )
}
```

Export uses only the saved inventory. Defaults bound one invocation to 1 GiB of
transferred bytes, one hour and an average 8 MiB/s rate. SDK requests/timeouts
and local hashing/verification can extend the soft runtime limit; an EOF probe can read one extra byte from an
incorrect-length source. Set the byte/runtime budget large enough for the largest
object. Exhausted budgets leave transfers incomplete; rerun the same command.
Failed reads retry on a subsequent invocation, in addition to bounded SDK retries.
Verified existing files are hashed locally and do not require another S3 GET.

For verification after AWS account loss, use only:

```powershell
Invoke-CopyOutput {
    Invoke-NativeCommand -FilePath python -ArgumentList @(
        '-B', 'evidence_backup.py', 'verify', $backupDirectory
    )
}
```

This operation does not import boto3, resolve credentials or contact AWS. It
streams aggregate JSON counts and coverage; the Python `verify(directory)`
iterator additionally returns private per-key statuses and relationship findings.
Do not share those keys or private files through GitHub or dashboard output.
Exit 0 requires completed inventories, a stable-cutoff attestation, verified
files and no detected raw-link integrity/identity conflicts. Exit 1 includes
incomplete transfers, missing/corrupt files, metadata errors and uncertain
coverage. Missing or malformed manifest/checkpoint files fail the operation.

## Manifest and integrity contract

All metadata files contain a `value` and its SHA-256 over canonical JSON encoding.
Schema version 1 uses these files:

- `manifest.json`: original bucket, exact prefix scope, creation time, attested
  cutoff, per-prefix completed page/object/byte counts, continuation token,
  final-page status, sanitized errors and a rolling page digest.
- `inventory/<prefix-index>/<page-number>.json`: request/next tokens and every
  listed object's original key, size, ETag and last-modified time. Sequential
  page numbers, token continuity, ordering, digests and count/byte totals are
  checked. A page is durable before its manifest checkpoint is advanced.
- `objects/<SHA-256-of-UTF-8-key>.bin`: exact S3 bytes. Source keys are identities,
  never local paths; traversal strings, case differences, reserved Windows names
  and separator variants therefore cannot escape or alias filesystem paths.
- `objects/<same-id>.json`: receipt with original bucket/key/inventory identity,
  transfer state/error, exact content type and complete S3 user metadata. Successful
  receipts additionally hold byte length, computed SHA-256, ETag, last-modified
  time, version ID when returned and export time.

Inventory pages and individual receipts are bounded JSON documents; file hashing
uses 64 KiB reads. Archive-sized inventories or payloads are never loaded into
memory. An ETag is used as an opaque conditional GET identity, not a universal
content checksum. GET size/ETag/last-modified must match listing; raw provenance
`sha256` and a returned full-object S3 SHA-256, when available, must match downloaded
bytes. A changed/disappeared object or failed request prevents verification.

The offline verifier checks each byte file, receipt identity/metadata checksums
and the inventory chain. It separately inspects normalized raw links (up to
1 MiB/record) for original bucket/key, hash and length agreement. Larger or
malformed normalized records remain preserved and are reported as unassessed or
unreadable. Raw objects with no normalized local receipt, normalized objects
without raw links and unavailable raw counterparts are reported distinctly;
absence of a local counterpart does not establish source corruption or absence.
Source bytes are never repaired or rewritten. Investigate all relationship
warnings before accepting recovery readiness, even when byte verification passes.

Metadata digests detect accidental inconsistency; they are not signatures and
cannot detect coordinated malicious rewriting of evidence and its manifest.
Keep the verified manifest/receipts with each independent encrypted copy and
protect an independently trusted copy of the manifest if authenticity is needed.
A successful listing cannot prove that AWS returned no omitted objects, that
the collector was paused, or that history had not already been lost. Completeness
is conditional on valid source responses, the recorded cutoff and every
inventoried object being verified. No historical-loss guarantee is made.

## Interruption and recovery

One `writer.lock` prevents overlapping inventory/export invocations. Verification
should run while no writer is active. A caught interruption removes the lock;
after a killed process, confirm no writer remains before manually removing only
that lock. Do not clear a lock merely because an operation is slow.

Downloads use disposable `.part` staging files. A verified staging file gets a
durable `prepared` receipt before an atomic, no-overwrite hard-link publication.
A rerun verifies a published prepared file and finalizes its receipt without
another GET; a complete prepared staging file is reverified and published locally
as well. A partial staging file is never accepted as final evidence and is
restarted. Files with no trustworthy receipt, altered final files and corrupted
metadata are left intact for diagnosis. Do not delete them to force success:
preserve the directory and use a new backup directory, or resolve the specific
conflict under separately approved recovery instructions. A verified receipt
whose file is missing is reported; reruns do not silently repair it.

For an interrupted inventory checkpoint, an already written page must match the
new response exactly. A conflicting page is retained and fails listing. Preserve
it and begin a fresh directory under a stable cutoff. Diagnostics store fixed
contract errors or exception class names, never arbitrary SDK messages, payloads
or credentials. The trusted local directory must not be modified concurrently
by other tools/users; this is not protection against a hostile filesystem writer.

## Independent copies and restoration prerequisites

The approved Issue #23 architecture has exactly two persistent evidence copies:
canonical S3 evidence and the independent Azure backup. Preserve the whole
baseline archive, including manifest, inventory, receipts and payloads. Retain the exact collector/schema commit and configuration
needed to interpret history separately, without secrets.

Live restoration remains separate work: approve an isolated private destination,
original-identity/location mapping, conditional no-overwrite restoration and
representative success/failure/empty/raw-only tests, then measure recovery gaps
and duration. Preserve embedded original bucket/key references. Do not restore
stale polling control or clear a halt automatically; use
[deployment recovery safeguards](deployment.md#polling-safeguards-and-recovery).
No restore command, external synchronization or backup schedule is provided here.

## Offline partitioned coverage (Issue #23, Slice 1)

`incremental_backup.py` replaces the monolithic planner with one bounded shard
at a time. It provides no SDK, network operations, archive extraction, evidence
transfer, credentials or scheduling. The collector and baseline remain unchanged.

### Baseline trust and recovery

`build_baseline_index(directory, index_directory=..., verified_archive_sha256=...)`
consumes a safely extracted, quiescent `evidence_backup.py` directory. The later
approved bootstrap must verify the downloaded archive **before extraction**
against trusted SHA-256
`e57758215de330b7f9db1fd8a75754ba2161764f32824cfb2485bcf639cef5f8`.
The argument attests to that check; it cannot establish extraction origin.

Construction verifies both completed inventories, UTC cutoff, page digest/token
chains, every verified receipt, exact payload hash/length and metadata consistency.
Raw relationships must be `normalized_receipt_present` or
`no_normalized_local_receipt`. Normalized relationships must be `raw_link_verified`
or `no_raw_link`. Every other outcome is rejected, including missing/unverified
raw counterparts, unreadable/malformed links, mismatches and links unassessed
because of the verification size bound. Legitimate raw-only evidence and
normalized failure records without a raw link remain valid.
Missing, corrupt, prepared or unindexed evidence is rejected. It publishes the
root only after all baseline verification and 256 shard writes succeed. Failed
builds have no usable root; preserve diagnostics and restart in a fresh directory.
The one-time baseline limits remain 100,000 objects, 10,000 pages and 100 GiB of
payloads; these do not limit ongoing incremental growth.

Original bucket/key, content type, all user metadata, optional source version ID,
listing fields, SHA-256 and byte length remain in the archive's full receipts.
The index references those records instead of duplicating their metadata. Recovery
requires the unchanged baseline plus all referenced immutable incremental batches
and receipt manifests; preserve embedded original source identities. There is
no migration of the discarded version 1/2 derived index; rebuild this derived index from
the verified baseline before later transfer work.

### Version 3 index schema

All files use the existing canonical JSON `{value, sha256}` checksum wrapper.
Checksums detect accidental corruption, not forgery; trust the index's origin and
keep the local snapshot quiescent during a sweep.

`index.json` holds `schema_version=3`, source `bucket`, the two canonical
`prefixes`, baseline cutoff/count/bytes, a `batches` map and **all 256** shard
descriptors. Each descriptor stores its exact immutable `location`
(`shards/hh/<value-sha256>.json`), shard value checksum, record count,
baseline object count and baseline byte total. Totals must match the baseline.
A root read alone does not verify the referenced shard files. Batch identifiers
use 1 to 64 ASCII letters, digits, dots, underscores or hyphens. Blob/member
references are bounded to 1,024 UTF-8 bytes and reject path traversal components.

Each referenced shard revision holds version, bucket, shard ID and `entries`.
Readers use the root's location, require its content-addressed path to match the
checksum/shard ID, and verify both wrapper and descriptor checksums. Each compact entry
is `[kind, uuid_hex, revision_fingerprint, batch_id]`:

- `kind` is `r` (raw `.bin`) or `n` (normalized `.json`).
- `uuid_hex` is exactly 32 lowercase hexadecimal characters; `hh` is its first
  two characters. Accept any UUID version because `persist` accepts UUID objects;
  the live handler generates UUIDv4. Unknown/nested/uppercase key formats fail.
- The fingerprint is SHA-256 of canonical JSON containing **listing** size,
  opaque ETag and normalized UTC LastModified. It is a revision-comparison digest,
  **not** a content checksum or guarantee against privileged mutation.
- `baseline` refers to the trusted unchanged archive. Derive the full receipt
  member as `objects/<SHA-256(original source key)>.json`; its existing receipt
  verifies the corresponding `.bin` payload.
- Incremental batches have kind `increment`, immutable `incremental/` blob name,
  verified batch SHA-256 and a `receipts_member` path. That receipt manifest maps
  `kind/uuid_hex/revision_fingerprint` to the full verified receipt and payload
  reference. Many objects may share one archive/batch; no per-object Azure
  metadata blob is required. The future transfer publisher must verify manifest
  identities, payloads and destination integrity before adding index coverage.

Different preserved revisions may share a source key. Duplicate identities,
conflicting baseline revisions, unknown batches, missing records, wrong-shard
entries and mismatched descriptor checksums fail closed on shard read. Existing
baseline entries are retained when incremental revisions are appended. Index
publication after live transfers remains outside Slice 1.

`stage_shard_revision` validates and flushes a shard, then publishes its exact
content-addressed location using a no-overwrite hard link. An existing location
is reusable only if its checksummed contents match exactly. Changed shards get
new locations; unchanged revisions can be reused. `publish_index` verifies all
256 referenced shard revisions and baseline totals before atomically replacing
local `index.json`. An interrupted stage or publication before root replacement
leaves the previous root readable. Readers holding an older root snapshot still
resolve its unchanged shard revisions after a later root commits. No full-catalog
copy is needed. Local publication requires a single writer and uses the existing
lock/fsync/atomic replacement contracts; it does not claim power-loss guarantees
beyond the filesystem or implement Azure conditional root publication.

Replacement `publish_index` requires an existing valid committed root. It
validates the old and candidate snapshots one shard at a time, preserving the
source identity, baseline cutoff/count/bytes/archive reference, each shard's
baseline byte totals, and every existing batch descriptor. Every committed entry
must remain unchanged, including its revision fingerprint and batch assignment.
No new entries may claim historical baseline coverage. Only new verified
incremental batches and revisions may be appended. A rejected replacement leaves
the committed root and its immutable revisions untouched. Initial baseline
construction remains a separate bootstrap, not a replacement-publication bypass.
The comparison retains at most two bounded shard entry sets plus the small root
snapshots, never a global catalog. Verification of newly transferred evidence and
Azure conditional publication remain later integration responsibilities.

Superseded/unreferenced index metadata requires bounded retention and cleanup
in the later Azure integration. Retain revisions referenced by committed roots,
active readers and pending publication/recovery work until a separately reviewed
metadata retention policy permits cleanup. This requirement authorizes no evidence
deletion or cloud cleanup now. Immutable baseline/incremental evidence is separate
from disposable index metadata.

### Full-prefix sweep and interruption

`plan_reconciliation(index_directory, inventories, output_directory=...)` consumes
one iterable of saved-format inventory pages for **each complete canonical
prefix**, starting from token `None`. Pages have `request_token`, `next_token`
and `objects` with `key`, `size`, `etag`, `last_modified`. Supply streaming page
iterables; do not materialize a whole source inventory in the caller. Require ordered unique
keys, consistent token chains, and a terminal page even for an empty prefix.
Do not feed only narrow shard listings: they can hide unknown source key formats.

The planner validates and routes observations to disposable per-shard JSONL
scratch files, then reads and reconciles one coverage/observation shard at a time.
It does not build or copy a growing global catalog. The returned summary has only
256 shard descriptors/counters; detailed results remain in per-shard output files.
All index/source inputs are read-only, and returned values have no mutable aliases
into inputs. Keep private temporary index/planning files outside Git; do not
retain them in GitHub artifacts or caches as another persistent evidence copy.

A plan shard counts apparently unchanged `already_covered` revisions, lists `new`
and `changed` transfer candidates, and references preserved revisions
`previous_revision_not_visible` in the current listing. Matching any verified
preserved revision avoids transfer. Absent raw/normalized counterparts do not
block independent coverage. Disappearance never deletes preserved evidence.

`complete=True` requires both prefix scans and **every** shard to succeed. The
planner always reports `consistent_cutoff=False`: a live scan is provisional,
not a point-in-time snapshot. Incomplete sweeps report their error and completed
work; `read_plan_shard` refuses classifications until the whole sweep completes.
Interrupted output is diagnostic, not a resume cursor. Restart both prefix scans
in a fresh output directory; independently verified index coverage survives.
Never skip by UUID order, timestamps or the historical cutoff. ETags are not
content hashes; metadata-only or other undetectable mutations remain possible.

### Capacity and operational limits

The default design horizon is **five years** at 2,880 new objects/day, beginning
with 4,782 baseline objects: 5,260,782 objects, averaging 20,550 per shard under
uniform UUIDv4 distribution. The per-shard limit is 65,536 source objects and
65,536 preserved index revisions: round up three times the five-year mean to the
next power of two. Extra historical revisions consume this same capacity.
Distribution skew and changes can exhaust a shard sooner; no unlimited-growth
or exact time-to-exhaustion guarantee is made.

Defaults retain 16 MiB limits for each serialized index shard and observation
partition, 4 MiB for root metadata, 8,192 immutable batch references, 262,144
source pages, 4 GiB total source spool, 4 GiB total result-shard output, and a
soft one-hour runtime limit. Result shards allow 64 MiB because changed/missing
revision reports are larger than compact index entries. Inputs/outputs must be
fresh, private directories. A metadata bound may be reached before a record
bound. Limits never authorize silently dropping evidence; failures remain
incomplete. Runtime checks cannot preempt a blocking caller iterator or filesystem
operation. A page-limit failure may consume one additional supplied page.

Synthetic measurements on 2026-10-11, using 14-character daily batch IDs and
paired raw/normalized UUIDs (decimal MB/GB below; limits above use MiB/GiB):

| Scenario | Records/shard | Serialized shard | Peak traced allocations | Measured core time | Projected total index | Daily read + full rewrite |
|---|---:|---:|---:|---:|---:|---:|
| One year | 4,125 | 0.516 MB | 7.2 MB | ~0.5 s | 132 MB | 264 MB |
| Five years | 20,550 | 2.57 MB | 34.6 MB | ~2.8 s | 658 MB | 1.32 GB |
| Configured boundary, unchanged | 65,536 | 8.19 MB | 112 MB | ~8.8 s | n/a | n/a |
| Boundary, every revision changed, 64-character batch ID | 65,536 | 11.47 MB | 245 MB | ~12.8 s | n/a | n/a |

The changed-boundary report serialized to 52 MB, below the 64 MiB report limit.
A separate bounded filesystem sweep of 20,550 synthetic objects successfully
processed all 256 shards in ~10.8 s under tracing, with ~35 MB peak allocations,
3.37 MB source spool and 59 KB result-shard output. This is a local measurement,
not a five-year end-to-end timing guarantee. Reserve at least 512 MiB of worker
allocation headroom plus interpreter/OS/caller overhead; `tracemalloc` is not RSS
and the code enforces count/byte budgets rather than a hard memory quota.

Total index estimates multiply the measured compact entry size by projected
object count and add shard headers. Add the small root/batch table; these figures
exclude payloads and full receipt manifests. Projected five-year source spool is
~863 MB. Three times the five-year mean leaves room for modest skew and extra
revisions, but skewed imports, accelerated cadence, unusually long fields or
frequent source mutations require another capacity check. Full-prefix scan work
and compact index I/O still grow linearly with retained history.

`tests/test_incremental_backup.py` contains reproducible bounded synthetic
measurements for one-year/five-year representative shards and the capacity
boundary. Measurements include fixture allocation, serialization/decoding,
validation and reconciliation under `tracemalloc`; they are not live evidence
measurements, RSS guarantees or cloud timing measurements. Total index and daily
transfer estimates are projections, not observed bills. Full provenance receipts
are stored once in immutable batches. A future daily sweep needs the compact
index once, with at most one rewrite per affected shard after verified transfers;
never fetch/rewrite it for every page or object. Read plus full-rewrite estimates
are conservative when every shard changes; unchanged shards need no rewrite.
Capacity review must consider revision counts, serialized/spool sizes and runtime
before limits are approached. Adjust bounds only after representative validation.

Final review corrections on 2026-10-12 leave compact entry sizes, partitioning,
resource limits and the five-year growth assumptions unchanged. Schema version 3
adds approximately 24 KB of immutable locations to a 256-shard root (about 48 KB
extra for one root read plus rewrite). Publication adds one bounded local
verification pass over referenced shards before committing the root; this does
not require fetching each shard repeatedly from Azure. Prior core scalability
measurements above remain representative; current offline suites repeat them.
