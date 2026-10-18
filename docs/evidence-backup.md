# Historical evidence export and local verification

[Issue #19](https://github.com/epmelito/TornRedeye/issues/19) provides offline-tested
tooling in `evidence_backup.py`. Passing synthetic tests does **not** establish
that a live backup exists. Manual baseline and incremental backups and recovery
verification have completed. Verified scheduled-backup milestones are recorded
under [scheduled operation](#scheduled-operation-disabled-by-default).
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

Further live restoration requires separate approval: use an isolated private destination,
original-identity/location mapping, conditional no-overwrite restoration and
representative success/failure/empty/raw-only tests, then measure recovery gaps
and duration. Preserve embedded original bucket/key references. Do not restore
stale polling control or clear a halt automatically; use
[deployment recovery safeguards](deployment.md#polling-safeguards-and-recovery).
No restore command is provided here. The daily integration is described under
[scheduled operation](#scheduled-operation-disabled-by-default).

## Incremental backup: immutable batch completion (Issue #23)

AWS S3 is primary; Azure Blob is the sole persistent external evidence copy.
`incremental_backup.py` and `incremental_transfer.py` are offline-testable,
SDK-free functions with injected clients. `incremental_runner.py` adds the thin
S3/Azure provider adapters and explicitly gated executable described below. For
the daily GitHub Actions integration and its verified milestones, see
[scheduled operation](#scheduled-operation-disabled-by-default).
Run exactly one publisher at a time. There is no remote index, mutable root, root snapshot,
compare-and-swap publication or remote index partitioning.

### Trusted baseline and disposable coverage

The original `baseline/yata-cutoff-20261011.7z` remains unchanged. Its trusted
SHA-256 is `E57758215DE330B7F9DB1FD8A75754BA2161764F32824CFB2485BCF639CEF5F8`.
During a separately authorized one-time bootstrap, verify the archive hash before
safe extraction, then call `build_baseline_catalog(extracted_directory,
output_directory=private_directory, verified_archive_sha256=trusted_hash)`.
The hash argument attests to that prior check; it cannot check an unavailable archive.

The builder reuses historical inventory-page, receipt, content and relationship
verification. Raw relationships must be `normalized_receipt_present` or
`no_normalized_local_receipt`; normalized relationships must be `raw_link_verified`
or `no_raw_link`. Missing, unverified, malformed, mismatched and unassessed links
fail closed. Raw-only evidence and normalized failure records with no link remain
valid. Missing/inconsistent pages, receipts, payloads, extra unindexed evidence,
invalid totals or incomplete inventories cannot establish a baseline catalog.

The compact `baseline.json` contains source bucket, both prefixes, archive identity,
cutoff, counts, bytes and rows `[kind, uuid_hex, revision, content_sha256, byte_length]`.
Original content type, all user metadata, timestamps, source ETag/version ID and
original archive-member references remain in the verified archive's receipts.
Key and receipt-member names are deterministically recovered from each compact row.
Pin the catalog value digest independently after verification. Historical bootstrap
may create `baseline/yata-cutoff-20261011.coverage.json` once; routine runs only read
it and require its pinned digest. A self-checksum alone does not establish trust.
No Azure bootstrap write is provided here; tests build a synthetic local archive tree.

#### One-time offline archive-to-catalog procedure

The existing builder and installed 7-Zip are sufficient; no new repository script
or dependency is needed. This procedure describes archive-to-catalog preparation;
completion evidence is recorded separately. It needs the actual approved archive already
present locally. Do not fetch cloud evidence or publish anything in this work package.
Use a fresh private staging directory outside every Git tree, sufficient free space,
and a trusted local 7-Zip installation. Keep archive/staging files quiescent and
access restricted throughout. Local staging is temporary working data, not a third
persistent backup. Preserve failed staging for diagnosis; no cleanup command is
provided here.

Run these steps from the repository root in PowerShell. The helpers below capture
command evidence and fail on nonzero native exit codes. Record their successful
output separately from Azure; stop on any error, unsupported layout or unexpected
entry rather than relaxing checks or accepting a partial catalog.

1. Configure local paths, verify the trusted **archive byte hash before extraction**,
   then inspect its technical member listing. `memberPrefix` is empty when
   `manifest.json` is at archive root; otherwise set it to the single simple wrapper
   directory actually shown by `7z l` (for example `yata-cutoff-20261011/`). The real
   archive layout has not been checked in this work package.

   ```powershell
   . .\.chatgpt\powershell-helpers.ps1
   $sevenZip = 'C:\Program Files\7-Zip\7z.exe'
   $baselineArchive = 'D:\PrivateEvidence\incoming\yata-cutoff-20261011.7z'
   $bootstrapDirectory = 'D:\PrivateEvidence\bootstrap-20261011'
   $memberPrefix = '' # set only after reviewing the archive listing
   $trustedArchiveSha256 = 'E57758215DE330B7F9DB1FD8A75754BA2161764F32824CFB2485BCF639CEF5F8'
   Invoke-CopyOutput {
       if ((Get-FileHash -LiteralPath $baselineArchive -Algorithm SHA256).Hash -cne $trustedArchiveSha256) {
           throw 'Untrusted historical archive; do not extract'
       }
       Invoke-NativeCommand -FilePath $sevenZip -ArgumentList @('l', '-slt', '-ba', '--', $baselineArchive)
   }
   ```

2. Recheck the hash and validate member names/types before writing any extracted
   files. Only the historical manifest, inventory pages, object receipts/payloads
   and their directories are allowed. Reject duplicate/case-colliding names,
   absolute/traversal paths, links, reparse points, alternate streams, encrypted
   members and unsupported attributes. These checks intentionally stop on an
   unexpected layout; they do not sanitize or flatten paths. Bound the listing to
   210,008 entries (builder defaults: 100,000 objects, two files/object and
   10,000 inventory pages, plus directories/manifest) and 100 GiB uncompressed (metadata included) before integrity
   testing/extraction. Do not use `-spf` or overwrite an existing extraction tree.

   ```powershell
   $bootstrapState = @{ Verified = $false; ExtractionDirectory = (Join-Path $bootstrapDirectory 'extracted') }
   $privateDirectoryCheck = @'
   import sys
   from evidence_backup import _lock
   with _lock(sys.argv[1]):
       pass
   '@
   Invoke-CopyOutput {
       if ((Get-FileHash -LiteralPath $baselineArchive -Algorithm SHA256).Hash -cne $trustedArchiveSha256) {
           throw 'Archive changed; do not extract'
       }
       if ($memberPrefix -and ($memberPrefix -cnotmatch '^[A-Za-z0-9_-]+/$' -or
           $memberPrefix.TrimEnd('/') -match '^(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])$')) {
           throw 'Unsupported archive wrapper'
       }
       $listing = @(Invoke-NativeCommand -FilePath $sevenZip -ArgumentList @('l', '-slt', '-ba', '--', $baselineArchive))
       $records = @(($listing -join "`n") -split '\r?\n\s*\r?\n' | Where-Object { $_.Trim() })
       if ($records.Count -eq 0 -or $records.Count -gt 210008) { throw 'Archive entry bound exceeded' }
       $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
       $directories = @('inventory', 'inventory/0', 'inventory/1', 'objects') | ForEach-Object { $memberPrefix + $_ }
       if ($memberPrefix) { $directories += $memberPrefix.TrimEnd('/') }
       $filePattern = '^' + [regex]::Escape($memberPrefix) + '(manifest\.json|inventory/[01]/[0-9]{8}\.json|objects/[0-9a-f]{64}\.(json|bin))$'
       [long]$expandedBytes = 0
       foreach ($record in $records) {
           $fields = @{}
           foreach ($line in ($record -split '\r?\n')) {
               if (-not $line.Trim()) { continue }
               if ($line -cnotmatch '^([^=]+) = (.*)$' -or $fields.ContainsKey($Matches[1])) { throw 'Invalid archive listing' }
               $fields[$Matches[1]] = $Matches[2]
           }
           $name = $fields['Path'].Replace('\', '/').TrimEnd('/')
           if (-not $seen.Add($name) -or ($fields.Keys -match '(?i)Link|Stream|Reparse') -or
               $fields['Encrypted'] -ne '-' -or $fields['Size'] -cnotmatch '^[0-9]+$') { throw 'Unsafe archive member' }
           if ($fields['Attributes'] -ceq 'D') {
               if ($directories -cnotcontains $name) { throw 'Unexpected archive directory' }
           } elseif ($fields['Attributes'] -cmatch '^([ARHS]+|[ARHS]* -[rwx-]{9})$') {
               if ($name -cnotmatch $filePattern) { throw 'Unexpected archive file' }
           } else { throw 'Unsupported archive member type' }
           $expandedBytes += [long]$fields['Size']
           if ($expandedBytes -gt 100GB) { throw 'Uncompressed archive bound exceeded' }
       }
       if (-not $seen.Contains($memberPrefix + 'manifest.json')) { throw 'Historical manifest missing' }
       if (Test-Path -LiteralPath $bootstrapDirectory) { throw 'Use a fresh private staging directory' }
       Invoke-NativeCommand -FilePath python -ArgumentList @('-B', '-c', $privateDirectoryCheck, $bootstrapDirectory)
       Invoke-NativeCommand -FilePath $sevenZip -ArgumentList @('t', '-bd', '--', $baselineArchive)
       Invoke-NativeCommand -FilePath $sevenZip -ArgumentList @('x', '-bd', '-y', "-o$($bootstrapState.ExtractionDirectory)", '--', $baselineArchive)
       if ((Get-FileHash -LiteralPath $baselineArchive -Algorithm SHA256).Hash -cne $trustedArchiveSha256) {
           throw 'Archive changed during extraction; do not build or pin coverage'
       }
       $bootstrapState.Verified = $true
       Write-Output "archive_preflight=passed members=$($records.Count) expanded_bytes=$expandedBytes"
   }
   ```

3. Build only from the resulting verified archive tree. The builder performs the
   existing historical evidence/relationship validation and refuses incomplete or
   inconsistent coverage. It writes `catalog/baseline.json` only after validation.
   Check the exact bytes against the Azure decoder/wrapper and record both hashes:
   **catalog value digest** is the independent pin for `--baseline-catalog-sha256`;
   **wrapped file byte hash** and length are for exact upload/readback comparison.
   Neither is the historical 7z byte hash. Do not serialize the return value alone,
   add a BOM/newline, or regenerate the file through PowerShell text output.

   ```powershell
   $catalogBuild = @'
   import hashlib, json, sys
   from pathlib import Path
   import evidence_backup as backup
   import incremental_backup as coverage
   import incremental_transfer as transfer
   extracted, output, archive_hash = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
   value = coverage.build_baseline_catalog(extracted, output_directory=output,
                                          verified_archive_sha256=archive_hash)
   artifact = output / 'baseline.json'
   data = artifact.read_bytes()
   if data != transfer.wrapped(value):
       raise SystemExit('Catalog bytes differ from the Azure wrapper contract')
   pin = backup._digest(value)
   coverage.validate_baseline(transfer.decode(data), pin)
   print(json.dumps({'offline_catalog_verification': 'passed', 'bucket': value['bucket'],
       'baseline': value['baseline'], 'catalog_value_sha256': pin,
       'wrapped_file_sha256': hashlib.sha256(data).hexdigest(), 'wrapped_file_bytes': len(data),
       'artifact': str(artifact), 'destination_blob': coverage.BASELINE_CATALOG_BLOB}, sort_keys=True))
   '@
   Invoke-CopyOutput {
       if (-not $bootstrapState.Verified -or (Get-FileHash -LiteralPath $baselineArchive -Algorithm SHA256).Hash -cne $trustedArchiveSha256) {
           throw 'Successful extraction and unchanged trusted archive required'
       }
       $evidenceDirectory = Join-Path $bootstrapState.ExtractionDirectory ($memberPrefix.TrimEnd('/'))
       $catalogDirectory = Join-Path $bootstrapDirectory 'catalog'
       Invoke-NativeCommand -FilePath python -ArgumentList @('-B', '-c', $catalogBuild,
           $evidenceDirectory, $catalogDirectory, $trustedArchiveSha256)
   }
   ```

Only a successful actual-archive run establishes the production catalog/pin. Review
its original source bucket, cutoff, counts and bytes against the approved historical
record, and retain the observed pin independently of the Azure blob. No production
catalog digest or inventory measurements are supplied by this preparation.

For a separately authorized bootstrap, obtain the approved archive,
run this offline conversion, then create `baseline/yata-cutoff-20261011.coverage.json`
as a BlockBlob from the **unchanged `catalog/baseline.json` bytes**, using create-only
publication and independent persisted-byte readback. Verify length/file SHA-256,
wrapper and the independently recorded value pin; verify an existing destination
rather than overwriting it. The normal incremental adapter deliberately rejects
baseline writes: do not relax that guard or use the daily runner as bootstrap.
Credential/permission setup, publication and cleanup of private temporary staging
remain outside this package. Keep the historical 7z blob unchanged.

`reconstruct_coverage` consumes the pinned baseline and completed batch manifests
once into disposable private local JSONL files. It rejects duplicate/conflicting
revisions and writes `coverage.json` only after every record and partition succeeds.
Returned values do not permit modification of authoritative persisted input.
Local partitions use the UUID's first byte to bound memory, retaining the tested
256-way local routing: five-year growth averages 20,550 records per partition,
versus 5.26 million in a full in-memory catalog. These files have no remote
publication/retention contract and can be discarded and rebuilt at every restart.

### Complete reconciliation and transfer

`run_backup(s3=..., azure=..., bucket=..., baseline_catalog_sha256=...,
inventories=..., limits=Limits(), scratch_parent=private_ephemeral_directory)`
loads the pinned catalog, enumerates batch metadata, resumes pending uploaded
batches, reconstructs coverage, plans both complete canonical prefixes and transfers
new/detectably changed revisions. Listing inputs use the historical saved-page format:
`request_token`, `next_token`, `objects` with key, size, ETag and UTC LastModified.
They must start at the beginning and terminate for both prefixes. UUID ordering,
LastModified watermarks and the historical cutoff never exclude source objects.

The planner compares listing descriptors without HEAD requests for unchanged
objects. It distinguishes apparently unchanged covered revisions, new objects,
changed revisions and previously covered revisions no longer visible. Terminal
empty prefixes are valid observations, not failures; empty iterators, broken token
chains, unknown key formats, duplicates, interrupted or over-limit sweeps fail
closed. No new source transfer follows an incomplete sweep. Full-prefix live
listings are provisional, not consistent-cutoff evidence, even when complete.
ETags are opaque revision signals, not content hashes; undetectable privileged
metadata/content mutation is outside this guarantee.

Transfer retains conditional S3 `IfMatch` reads and checks returned size, ETag,
LastModified, available full-object checksum and raw metadata SHA-256. Streams hash
exact bytes, including empty bodies, without normalization. Original identity,
content type, all string user metadata, version ID and listing/read provenance are
stored in `receipts.json` inside a deterministic uncompressed ZIP. Raw and normalized
objects do not need counterparts. Greedy batches cross local partitions and respect
object-count and byte bounds.

Batch identity is SHA-256 of source bucket and sorted `kind/uuid/revision` references.
Destination names are:

- `incremental/batches/<batch>.zip.prepared.json`: immutable original size, ZIP
  SHA-256 and embedded receipt-manifest digest, created and readback-verified first.
- `incremental/batches/<batch>.zip`: create-only original payloads and full receipts.
- `incremental/batches/<batch>.zip.verified.json`: compact schema-2 completion,
  containing the prepared values, source bucket, `destination_verified` status and
  five-column coverage rows derived from independently verified persisted receipts.

Every metadata document uses the existing canonical `{value, sha256}` wrapper.
ZIP upload success is insufficient: read persisted bytes back, hash and compare
length/SHA-256, then validate exact regular ZIP members, embedded receipt checksum,
source mappings, metadata and each payload length/hash. The persisted-stream hash
is reused for outer ZIP verification; it is not redundantly recomputed. The
original archive hash is also reused across prepared-intent and upload verification. Recovery
from a local archive independently hashes the whole file when no stream observation
is supplied. A completion is create-only and readback-verified only after all evidence
checks succeed. Conflicting ZIPs, intents or completions are never overwritten.

### Enumeration, interruptions and recovery

The Azure adapter must provide bounded-timeout/no-hidden-retry `open_blob`,
create-only `create_block_blob` and complete lexicographic `list_blobs(prefix,
continuation_token, page_size)` results with `blobs` (name, byte size) and `next_token`.
Enumeration uses 5,000 names/page, validates token chains, names, counts and sizes.
Normal startup GETs only the baseline catalog and each compact completion once.
Every completed ZIP must be listed with the expected byte size; routine startup
does not repeatedly download old ZIPs, full receipts or prepared intents.
Completions are trusted-writer records authenticated by access controls, not signatures
or proof against a privileged writer. Same-size corruption after verification is
found during recovery verification, not every daily reconciliation.

An uploaded ZIP with a prepared intent but no completion is independently readback
verified against that original intent and its embedded receipts, then completed.
This survives changed batch composition or source disappearance without re-fetching
historical source bytes. An intent without a ZIP establishes no coverage and is
reported as unresolved; matching current candidates may retry it. The result
reports the remaining `pending_intents` after verified completions in the current
run, including newly prepared batches. Any remaining intent prevents `complete=True`,
even when the current source sweep succeeds or newer revisions are backed up. A
different batch composition does not resolve the original immutable intent. No
automatic deletion or abandonment is performed. A ZIP with neither
intent nor completion is ambiguous and fails closed. Retry attempts count against
limits, including unsuccessful reads/writes; lost upload acknowledgements verify the
persisted object before retrying. A failed later batch cannot invalidate earlier
immutable completions. An uncertain completion acknowledgement remains recoverable
at the next complete enumeration. Do not silently repair or delete conflicting evidence.

For demonstrable recovery, verify the baseline archive's trusted hash and safely
extract/verify its historical contracts. Enumerate and validate compact completions;
for each increment, call `Runner.inspect_archive(path, completion)` without a supplied
stream observation. It verifies ZIP size/SHA-256 and matches derived coverage rows
to the completion. Recover original bytes and full provenance from
those receipts. Preserve every source revision and distinguish unavailable current
source from recovery loss. Baseline plus all verified increments is the recovery set.
Do not read only the latest day's batch. No persistent local archive or third provider
is introduced. Previously committed remote index metadata is not used; no migration,
cloud cleanup or evidence deletion is authorized or performed here. Any existing
schema-1 verified receipts require a separately reviewed conversion through actual
persisted-archive verification; they cannot be silently trusted as schema-2 coverage.

A complete enumeration is not a globally atomic multiwriter snapshot. The former
root/CAS ordering guarantee is deliberately removed; serialize the publisher and
keep evidence/manifest writes create-only. The Azure integration must exclude
these artifacts from deletion/expiry and authenticate the writer. With no retained
root/history authority, disappearance of every artifact of a batch cannot be
detected from enumeration alone; neither checksums nor create-only writes protect
against privileged deletion or forged completion records. Failure to list, parse, validate or fit
resource limits is visible (`complete=False`, bounded sanitized error and counters).
A complete job never claims a consistent cutoff. Hard process termination may leave
private ephemeral scratch for runner cleanup; it is not a backup copy.

### Offline-tested operational entry point (Slice 3)

`python -B incremental_runner.py --help` is safe offline: imports are standard
library plus the engine, and no client or credential resolution occurs. The
executable requires `--bucket`, `--owner` (12-digit expected AWS bucket owner),
`--region`, `--azure-account`, `--azure-container`,
`--baseline-catalog-sha256` (independently pinned lowercase catalog value digest),
`--scratch-parent` (private directory outside Git) and separate live approval via
`--live-access-approved`. The flag is an execution gate, not authorization by itself.
Live execution requires separate approval. `--max-seconds` defaults to the one-hour soft limit;
engine object, batch, transfer and scratch bounds remain unchanged.

The CLI emits one JSON result and exits zero only for `complete=True`; failures,
incomplete inventories and unresolved prepared intents exit one. Argument parsing
errors exit two. Diagnostics expose controlled messages/status codes or exception
class names, never provider error bodies, arbitrary SDK messages or SAS values.
The existing private-directory writer lock is held across execution, preventing
concurrent CLI writers sharing that scratch path. Different paths/hosts still
require external serialization; there is no remote coordination.

The S3 adapter uses the project's existing external boto3 convention; no package
or dependency is installed by this slice. Only the explicitly gated client factory
imports boto3 and resolves already available AWS credentials. It configures 10-second
connect and 30-second read timeouts and one SDK attempt. Engine GET retries and
adapter LIST retries each allow three counted attempts; no additional adapter GET
retry loop exists. Both prefixes stream from the beginning with MaxKeys=1,000,
continuation tokens and terminal empty pages. Every LIST/GET includes
ExpectedBucketOwner; GET forwards the engine's IfMatch and original response/body,
including content type, user metadata, opaque ETag, LastModified and version ID.
Unknown errors/403/404/412 are permanent failures; transport errors and recognized
transient statuses can retry. Truncated source streams never establish coverage.

The Azure adapter uses standard-library HTTPS and Azure public-cloud Blob REST
version 2019-12-12, with an existing URI-encoded SAS supplied externally in
`TORNREDEYE_AZURE_SAS`. It does not generate, renew, provision or log credentials.
The token must already permit required container listing, evidence reads and
create-only writes. The daily integration generates this token using the
[OIDC permissions below](#cloud-identities-and-permissions); historical bootstrap
still requires separate authorization and permissions.
There is no Azure SDK dependency. Account/container configuration fixes the HTTPS
host and path. Redirects are rejected. LIST uses container comp=list, canonical
incremental prefix, maxresults and marker, then validates bounded XML; GET exposes
a closable bounded stream and requires content length, ETag and BlockBlob headers.
PUT streams <=64 KiB chunks with known Content-Length, x-ms-blob-type=BlockBlob
and If-None-Match=* in a single request. It never stages blocks, overwrites evidence,
replaces a root or modifies baseline blobs. Only BlobNotFound maps to an absent
object; container-not-found/authorization errors remain failures. Only the expected
create conflict codes map to existing-object verification. A lost PUT acknowledgement
continues through the engine's persisted-byte readback; provider success alone
still cannot establish coverage.

A missing baseline catalog fails explicitly with `bootstrap required` before any
S3 listing or transfer. A present catalog with the wrong independently supplied
pin fails closed. The compact catalog is read, never initialized or uploaded.
The historical archive is not downloaded, extracted or uploaded by the entry point.
Prepare the verified compact catalog
through a separately approved historical bootstrap before live use.

S3 LIST attempts are bounded to 262,144 across both prefixes/retries. Accepted
saved-format listing metadata is capped at 4 MiB/page and 4 GiB/run; the reported
`source_inventory.serialized_bytes` is canonical page size, **not measured AWS
wire bytes**. The CLI populates `operations.s3_list` with counted adapter attempts.
boto3 parses LIST responses before this application check. Azure XML
responses are capped at 8 MiB/page, with actual consumed response bytes reported
separately from the engine's streamed-body transfer counters; the existing 64-LIST
attempt cap bounds this additional traffic to approximately 512 MiB (plus a
one-byte oversize probe per failed page). XML declarations with DTD
or entities are rejected. SDK credential discovery/region redirect behavior is
outside application request counters and needs validation before live approval.
Transport timeouts are inactivity limits and runtime checks are soft between
operations; they do not forcibly interrupt a stalled/drip-fed SDK or socket call.

Offline validation uses fake SDK clients and fake HTTPS connections exclusively. Provider
response formats, pagination, error mapping, bounded streams, conditional writes,
source ownership, baseline failure, entry-point exit codes, corrupt readback,
lost acknowledgement and verified restart are exercised offline. Those tests alone
do not establish OIDC authentication, generated `rcl` permissions or hosted-runner
compatibility; completed live verification is recorded below.

### Scheduled operation: disabled by default

The first unattended scheduled backup was
[verified on October 14, 2026](https://github.com/epmelito/TornRedeye/issues/23#issuecomment-6173917639),
including AWS/Azure OIDC authentication, persisted-evidence verification and an
independent Azure inventory. Subsequent scheduled runs succeeded through
[October 18, 2026](https://github.com/epmelito/TornRedeye/actions/runs/39110394657);
that run completed both OIDC logins and the generated-SAS verified-backup step.
These are dated operational milestones, not guarantees of future execution or
complete recovery coverage. The configuration remains disabled by default:

`.github/workflows/daily-backup.yml` defines 03:23 UTC daily execution and manual
dispatch, both gated by the repository Actions variable `DAILY_BACKUPS_ENABLED`
being exactly `true`. An unset variable or any other value skips the job before
checkout, credential exchange or cloud access. Only repository ID `1409429829`,
owner ID `188345459`, and exact ref `refs/heads/main` are allowed. The gate does
not depend on a default-branch property in the event payload.
There are no pull request, push or tag triggers. Branch/default-branch changes
require coordinated review of the gates and both cloud trust policies.

One repository-wide concurrency group serializes scheduled and manual runs;
`cancel-in-progress: false` preserves the running backup. GitHub can replace an
older pending run with a newer pending run; this is not a daily delivery guarantee.
The group does not serialize a local CLI or another repository: keep those
writers stopped. GitHub schedules can be delayed/missed and can be disabled after
inactivity in public repositories. Failed/incomplete jobs must be checked through
Actions; no new monitoring or notification service is introduced.

The workflow uses a fresh GitHub-hosted Ubuntu 24.04 runner, Python 3.11, the
existing boto3 dependency (installed at a fixed version), and the runner's Azure
CLI. Provider actions and checkout/setup are pinned to verified full commit IDs.
There is no Azure SDK addition. `tools/daily_backup.py` requests a container
user-delegation SAS with `--auth-mode login --as-user --https-only`, read/create/list
(`rcl`) only, starting five minutes earlier for clock skew and expiring 75 minutes
after issuance. SAS fields are checked before passing it solely through the
runner child environment as `TORNREDEYE_AZURE_SAS`; no GitHub output/env file,
command argument or artifact contains the token.

The runner gets a 55-minute soft deadline; its subprocess is killed and reported
failed after 60 minutes, SAS generation times out after two minutes, and the job
has a 75-minute bound. AWS credentials last 75 minutes. These hard limits cover
stalled provider calls that the existing between-operation checks cannot stop.
No run succeeds unless both the runner exit code is zero and its JSON has literal
`complete: true`. A timeout can leave a remotely prepared or uploaded batch;
the next run uses existing verified restart/reconciliation, without deleting or
overwriting evidence. Completion still does not assert a consistent cutoff.

Temporary archives, manifests and captured stdout/stderr use owner-only scratch
under `RUNNER_TEMP`, outside checkout. Only fixed success/failure stage messages
enter Actions logs; the full JSON and provider diagnostics are discarded. Normal
exit removes scratch and an `always()` step removes the fixed scratch/Azure CLI
directories on failure. Azure/AWS login actions perform their credential cleanup.
Force termination may prevent cleanup; hosted-runner disposal is the final
boundary. No cache or artifact upload is used. Keep Actions step/runner debugging
and Azure CLI debug logging disabled for live execution; the wrapper rejects
`RUNNER_DEBUG=1`. Inspect failed private remote batches through a separately
authorized private diagnostic session, never by printing captured output in Actions.

#### Cloud identities and permissions

The requirements below remain applicable to future separately approved setup or
identity changes; initial scheduled operation has been verified as recorded above.
These are not commands to execute during offline implementation. Use no AWS
access-key secret, Azure client secret, account key or stored SAS.

Use issuer `https://token.actions.githubusercontent.com` for both providers.
GitHub's [current OIDC reference](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims)
requires immutable owner/repository IDs in the default subject for repositories
created after July 15, 2026. The
[repository metadata](https://api.github.com/repos/epmelito/TornRedeye) reports
creation on October 7, 2026 and the IDs above. Therefore the expected default,
case-sensitive subject for this job (which uses no GitHub environment) is:

```text
repo:epmelito@188345459/TornRedeye@1409429829:ref:refs/heads/main
```

At implementation time, this subject was inferred from GitHub requirements and
repository metadata, not an observed JWT. Repository subject customization could
change it. Access to the repository OIDC customization endpoint was unavailable
through the read-only connector used for implementation. Before future cloud
setup, reactivation or trust changes, inspect
the repository Actions OIDC settings (or authenticated read-only
`GET /repos/epmelito/TornRedeye/actions/oidc/customization/sub`) and confirm the
effective subject. If a separately approved token check is needed, compare only
`iss`, `aud` and `sub` privately; never log/store the full token. Do not adopt the
older names-only example or wildcard trust. Adding a GitHub environment also
changes the subject and requires new review.

AWS: use an IAM OIDC provider with audience `sts.amazonaws.com` and a dedicated
role allowing `sts:AssumeRoleWithWebIdentity` only for that provider. In the role
trust statement use exact `StringEquals` conditions:

```json
{
  "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
  "token.actions.githubusercontent.com:sub": "repo:epmelito@188345459/TornRedeye@1409429829:ref:refs/heads/main"
}
```

After confirming the subject, grant the role only `s3:ListBucket` on the canonical
bucket ARN with `s3:prefix` restricted to `raw/yata/jap/206/` and
`normalized/yata/jap/206/`, and `s3:GetObject` on those two object-prefix ARNs.
No writes, deletes, other prefixes, deployment or collector-control access.
Allow a 4,500-second role session. The runner sends ExpectedBucketOwner on every
LIST/GET; the action also checks the configured account ID. SSE-S3 needs no KMS
permission; deployed KMS encryption would require separate decrypt review.

Azure public cloud: use a dedicated Entra application/service principal (or
user-assigned managed identity), with an exact federated identity credential for
that issuer and subject and audience `api://AzureADTokenExchange`. Supply its
client/tenant/subscription IDs. Assign Storage Blob Delegator at the storage
account scope for `Microsoft.Storage/storageAccounts/blobServices/generateUserDelegationKey/action`.
Assign a custom role at the **existing backup container** scope, with no management
Actions and only these DataActions:

```text
Microsoft.Storage/storageAccounts/blobServices/containers/blobs/read
Microsoft.Storage/storageAccounts/blobServices/containers/blobs/add/action
```

Read includes listing. The documented create-new BlockBlob action is `add/action`;
`write` and `delete` are unnecessary and are excluded. See
[Put Blob authorization](https://learn.microsoft.com/en-us/rest/api/storageservices/put-blob#authorization)
and [Storage Blob Delegator](https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/storage#storage-blob-delegator).
The [user-delegation SAS contract](https://learn.microsoft.com/en-us/rest/api/storageservices/create-user-delegation-sas)
intersects the principal's permissions with signed SAS permissions; `c` permits
new BlockBlobs while `w` permits overwrites. Do not substitute Data Contributor,
which also grants write/delete. This container-scoped minimum allows reads and
new blobs anywhere in that container, including baseline names; runner path
guards restrict ordinary daily writes to `incremental/batches/`. It is not a
prefix-level cloud authorization boundary. Both identities trust default-branch
jobs generally, so protect `main` and review changes to code/workflows that can
request OIDC tokens; the activation variable is an operational gate, not IAM.

#### Configuration and activation procedure

Initial activation and live verification have completed as recorded under
[scheduled operation](#scheduled-operation-disabled-by-default). The following
setup and activation guidance is retained for future approved changes or
reactivation, not as a statement that the existing integration is inactive.

Repository Actions variables (no credential secrets):

| Variable | Required value |
| --- | --- |
| `DAILY_BACKUPS_ENABLED` | Exactly `true` enables eligible scheduled/manual jobs; unset or `false` skips them. Future configuration changes require applicable approval. |
| `BACKUP_AWS_ROLE_ARN` | Dedicated read-only OIDC role ARN |
| `BACKUP_AWS_OWNER` | Expected 12-digit canonical bucket/account owner |
| `BACKUP_AWS_REGION` | Canonical bucket region (`eu-north-1` for current deployment) |
| `BACKUP_AWS_BUCKET` | Canonical private evidence bucket name |
| `BACKUP_AZURE_CLIENT_ID` | Dedicated Entra principal client ID |
| `BACKUP_AZURE_TENANT_ID` | Entra tenant ID |
| `BACKUP_AZURE_SUBSCRIPTION_ID` | Existing storage subscription ID |
| `BACKUP_AZURE_ACCOUNT` | Existing public-cloud storage account name |
| `BACKUP_AZURE_CONTAINER` | Existing private backup container (`tornredeye-backups`) |
| `BACKUP_BASELINE_CATALOG_SHA256` | Independently verified lowercase catalog **value** digest, not archive/file SHA-256 |

Before future setup, reactivation or identity changes, separately approve OIDC
identity configuration and live OIDC/`rcl` integration verification. Reuse the
verified baseline/catalog pin; check
network/firewall access from a hosted runner, retention of batch artifacts, and
the selected identity permissions. Confirm installed Azure CLI token shape/expiry
and read/create/list interoperability, including existing-blob conditional PUT
error mapping and interrupted-run recovery with create-only permissions. Offline
tests cannot establish those live contracts; do not broaden permissions to hide
an authorization/error-mapping problem. Account SAS expiration policy may need to
accept the 80-minute signed interval (five-minute skew plus 75-minute lifetime).

For future separately approved setup or reactivation, after review/merge through
the separate GitHub workflow, keep the variable disabled while configuring the
approved identities/variables and checking effective subject
matching. Once live execution/recurring operation is explicitly approved, set the
gate to `true` and manually dispatch **main** to verify that setup or reactivation;
enabling the gate also permits the next daily schedule, so coordinate that window. Check
Actions outcome and privately verify the expected persisted evidence. Set the
gate to `false` to stop future jobs; it does not stop one already running. Do not
run a second local publisher while a job is active. The dated verification above
establishes the completed scheduled-integration milestone; future configuration
changes and recovery operations still require their applicable approvals.

### Resource bounds and cost comparison

Assessment performed before source edits on 2026-10-12. This is a modeled comparison,
not live inventory or an invoice. Inputs: documented 4,782 historical objects;
one-minute cadence gives an estimated 2,880 additional objects/day; 365 days/year;
uniform UUID routing, no extra changed revisions, one new batch/day and all 256 old
remote partitions changing. One batch/day requires daily payload <=64 MiB and <=4,096
objects: actual payload sizes are unknown and may require more batches. Preserve
explicit B (new batches), C (prior completed batches), S (old changed-partition writes),
P (new payload bytes), Z (new ZIP bytes including full receipts), M (old current index
bytes), U (old changed partition bytes) and H (all compact completion bytes).

A synthetic five-column completion row is 180 serialized bytes including separator;
old compact tuples were about 175 bytes for a 64-character batch ID. Projected old
current metadata uses the documented baseline model `M0=620258` plus `175*2880*d`.
New metadata uses `180*2880*d`, ~0.86 MB baseline rows plus bounded headers (~0.55 KB
per batch). These are decimal MB/GB; price conversion uses the provider's billing
units. Metadata user fields live inside ZIPs once, not repeated in daily coverage
GETs. Full ZIP receipts/overhead, unchanged in both designs, are additional storage.

| Modeled daily resource | Previous remote catalog | Completion manifests |
| --- | --- | --- |
| S3 LIST attempts | sum over prefixes ceil(objects/1000), at least one per prefix | unchanged |
| S3 GETs | new/changed objects plus retries | unchanged |
| Azure PUT attempts | `1+5B+S` | `3B` |
| Azure GET attempts | `258+6B+S` | `1+C+4B` |
| Azure LIST attempts | none for catalog startup | ceil(batch artifact names/5000), at least one |
| Azure outbound metadata bytes | approximately `M+U` plus roots/intents/proofs | `H+baseline` plus new intent/completion readbacks |
| New ZIP upload and Azure readback bytes | `Z` each direction | unchanged |
| Retained catalog metadata | accumulated immutable partition/root revisions | one compact completion per batch; no rewritten catalog |

For B=1/S=256, writes fall **262 to 3/day**. GETs change from 520/day to ~370 at
year one and ~1,830 at year five. Three artifact names/batch imply ~1 and ~2 LIST
pages respectively (pending artifacts increase this). AWS requests remain ~1,056
LIST + 2,880 GET/day at year one and ~5,262 LIST + 2,880 GET/day at year five.

| Projected quantity | Year one | Year five |
| --- | --- | --- |
| Objects | 1,055,982 | 5,260,782 |
| Old current metadata GET | 184.6 MB/day | 920.4 MB/day |
| New baseline/completion metadata GET | ~190 MB/day | ~948 MB/day |
| Old metadata Azure outbound, including partition readback | ~369 MB/day | ~1,841 MB/day |
| New metadata Azure outbound, including small new proof readbacks | ~190 MB/day | ~948 MB/day |
| Old retained immutable partition metadata, no cleanup | ~33.9 GB | ~840.9 GB |
| New retained compact coverage metadata | ~0.19 GB | ~0.95 GB |

Thus startup metadata alone rises ~3%, while total daily Azure metadata outbound
falls ~49%; savings are not merely fewer writes hiding full historical receipt
GETs. Retained old metadata is `sum(M0+175*2880*t, t=1..d)` assuming all partitions
rewritten daily and no cleanup; roots add further bytes. With many smaller batches,
new GET/header counts grow as C, whereas old partitions may be rewritten more than
once/day. Neither design downloads historical payloads during a normal run. Evidence
storage is `baseline + sum(Z)` in both, not just compact metadata. AWS-to-runner
transfer is P plus listing bytes; runner-to-Azure upload is Z; Azure outbound includes
Z readback and coverage metadata. Runner-region/provider outbound charges, retrieval
tier charges, allowances, retries and billing-meter rounding must be priced separately.

Use the dated 2026-10-11 Stockholm AWS evidence/units above, not invented rates:
LIST `$0.005/1000`, Standard GET `$0.0004/1000`. The modeled LIST+GET request
subtotal is ~$0.006432/day at year one and ~$0.027462/day at year five, unchanged
by this refactor and excluding transfer/retries. Standard source storage remains
`source_GB_month * $0.023`; chargeable ordinary internet transfer uses the above
`$0.09/GB` first-tier planning rate and allowances. These dated prices require
recheck before live operation. Authoritative references are [AWS S3 pricing](https://aws.amazon.com/s3/pricing/)
and the regional offers above; [Azure Blob pricing](https://azure.microsoft.com/en-us/pricing/details/storage/blobs/)
sets tier/region/redundancy-dependent storage and per-10,000 transaction pricing.
No Azure rate is asserted: account region, tier, redundancy, outbound destination,
allowances and runner provider remain unverified.

With Azure rates r_get/r_put/r_list per 10,000 operations, r_out per chargeable
billing GB and r_store per GB-month, price each design as:
`GET*r_get/10000 + PUT*r_put/10000 + LIST*r_list/10000 + outbound_GB*r_out + retained_GB_month*r_store`.
At five years, extra ~1,310 reads/day trade against ~259 fewer writes/day, two LIST
requests and ~0.89 GB/day less metadata outbound plus much less retained metadata.
If all outbound/storage differences are free, transaction savings alone depend
on r_put/r_get being above ~5.1 (plus LIST charges). Total savings cannot be stated
without actual rates. This bounded request increase does not create disproportionate
metadata downloads; no additional remote index is needed. An interrupted uploaded
batch adds prepared GET + ZIP GET + completion PUT/readback, without a source GET
or ZIP re-upload. A prepared-only retry may re-fetch that bounded batch; failures
before the intent persists may repeat source reads. Price every actual attempt.

Defaults: 16 MiB/object, 4,096 objects and 64 MiB payload/batch, 128 MiB ZIP,
16 MiB metadata document, 4 GiB private scratch, 8 GiB streamed body transfer,
8,192 S3 GET attempts, 8,192 Azure GET attempts, 64 Azure LIST attempts,
1,024 Azure PUT attempts, three attempts/request, one-hour soft deadline.
The raised read-attempt bound covers ~four batches/day over five years (7,300
completion reads plus bootstrap/new work); it is not unlimited growth. All client
requests need bounded transport timeouts and listing-response byte accounting; a blocked adapter cannot be stopped by
between-operation deadline checks.

Local coverage/source limits are 65,536 records/objects per partition, 16 MiB
partition files, 64 MiB result partitions, 262,144 source pages, 8,192 batch references
and a 4 MiB local summary. Revisions, UUID skew or large batch-header tables can hit
limits sooner than the modeled horizon. The unified run allocates thirds of scratch
for coverage, source spool and plan; retained derived files count against transfer
scratch too. Coverage rows with batch IDs are ~247 bytes, so year-five local coverage
is ~1.30 GB; missing/changed result volume may reach the scratch bound and visibly
fail. Do not claim arbitrary full five-year disaster enumeration fits a daily-run
budget. The focused memory test measures a five-year representative partition and
the 65,536-record changed boundary; the first focused run measured ~41.3 MB traced allocation for 20,550 changed
records and ~133.3 MB at the 65,536-record changed boundary. These are Python
allocations on this workstation, not process RSS or a live service measurement.

Validation: `python -B -m unittest discover -s tests -p "test_incremental*.py"`,
then `python -B -m unittest discover -s tests`. Synthetic fixtures cover baseline
corruption/relationships, complete and partial scans, growth/bounds, exact/empty
bytes, opaque ETags, full metadata, changed revisions, conditional-read failures,
checksums, immutable conflicts, persisted verification, paginated enumeration,
interrupted uploads and completion retries. No historical archive or live provider
is required.
