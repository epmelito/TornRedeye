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

Required read permissions are `s3:ListBucket` on the existing private evidence
bucket, restricted by `s3:prefix` to exactly `raw/yata/jap/206/` and
`normalized/yata/jap/206/`, and `s3:GetObject` on objects under those prefixes.
No PutObject, DeleteObject, polling-control, logs or deployment-artifact access
is needed. Existing bucket encryption is SSE-S3; future KMS encryption would
require a separately reviewed decrypt permission. This inventories current
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

Follow the cost-protection document's owner-controlled encryption and two
independent copies outside the source AWS account, with separately recoverable
keys. Copy the whole directory, including manifest, inventory and receipts;
verify each decrypted copy offline. Losing encryption keys makes intact ciphertext
unrecoverable. Retain the exact collector/schema commit and configuration needed
to interpret history separately, without secrets.

Live restoration remains separate work: approve an isolated private destination,
original-identity/location mapping, conditional no-overwrite restoration and
representative success/failure/empty/raw-only tests, then measure recovery gaps
and duration. Preserve embedded original bucket/key references. Do not restore
stale polling control or clear a halt automatically; use
[deployment recovery safeguards](deployment.md#polling-safeguards-and-recovery).
No restore command, external synchronization or backup schedule is provided here.
