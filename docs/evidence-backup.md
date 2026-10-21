# Independent evidence backup and isolated recovery

Issue #53 starts a fresh operational lineage; this implementation is offline.
AWS S3 remains primary and existing Azure Blob Storage remains the one independent
persistent backup. Temporary local exports/restores are disposable private staging.
No cloud write, schedule change, deletion or cutover has been performed.

## Scope and trust

Complete inventories cover only:

- `evidence/live-v1/`: raw bytes and all attempt documents, including empty,
  malformed, failed, missing-target, bodyless and raw-only partial attempts.
- `forecasts/live-v1/records/`: immutable forecasts, issuance acknowledgements,
  outcomes and checkpoints, across explicitly named forecast epochs. Embedded
  input snapshots, object receipts, hashes and predecessor links preserve provenance.

Mutable `forecast-state/` heads/leases and source polling control are excluded
from automatic recovery. Unknown key formats fail; no status-based payload filter
or historical-prefix fallback exists. Both complete prefix inventories are required.

A deterministic version-3 empty-lineage anchor binds bucket, lineage and exact
scope. It asserts no prior backup coverage and references no historical archive.
Compute its digest offline for the approved bucket:

```powershell
python -B -c "import evidence_backup as b, incremental_backup as i; print(b._digest(i.empty_anchor('<bucket>')))"
```

Independently pin that digest as `BACKUP_ANCHOR_SHA256`. On the first separately
authorized run, the runner creates and reads back the exact anchor at
`operational/live-v1/anchor.json`; it rejects a conflicting existing anchor.
Remote self-checksums detect corruption, not forgery by a privileged writer.
Authenticated destination ownership and independently recorded pins remain necessary.

## Transfer, coverage and bounds

Reuse the existing conditional S3 reads, bounded streams, immutable ZIP_STORED
batches, prepared intents, destination readback hashes and verified completions.
Azure objects live under `operational/live-v1/batches/<batch-sha256>.zip` with
`.prepared.json` and `.verified.json` companions. Completion is published only
after the persisted archive and every payload/receipt mapping verify. Lost write
acknowledgements and archive-without-completion interruptions are recoverable.
Prepared-only intents establish no coverage and cannot be silently skipped.

Coverage identifies full canonical keys plus revision descriptors; ETags are not
content hashes. Receipts retain original bucket/key, bytes, SHA-256, length,
ContentType, metadata, ETag, LastModified and available VersionId. Immutable source
changes/disappearances fail the run for inspection; retained backup receipts are
not deleted or rewritten. Historical r/n/R/N identities and bootstrap catalogs
are absent from the active engine.

Each daily run fully reconciles fresh-lineage S3 metadata against verified compact
completion records, using the existing disposable 256 key-hash partitions. It
transfers uncovered payloads only, without downloading old archives on normal
runs. This deliberately retains full metadata inventories for backup: late writes
into old retrieval partitions must be found. Forecast processing uses bounded
hour queries instead. Daily metadata work grows with this lineage; it is not
claimed constant-cost or a point-in-time snapshot while writers run.

Existing request, runtime, disk, manifest, object and batch limits remain enforced;
capacity exhaustion fails visibly. Default batch payload limit is 64 MiB, archive
128 MiB, individual object 16 MiB. Source LIST requests/bytes have adapter limits.
A complete live sweep accounts for returned inventory, not an atomic cutoff.
A sealed completeness claim requires pausing/draining all writers and recording
an authorized cutoff. Failed pages, transfers or unresolved intents prevent success.

## Invocation and permissions

The existing daily GitHub workflow remains guarded, serialized and disabled unless
explicitly activated. It retains its 03:23 UTC cadence and short-lived AWS/Azure
OIDC authentication. Replace the old baseline configuration with the anchor pin.
The CLI is `incremental_runner.py --help`; live access still requires separately
approved credentials/configuration and `--live-access-approved`. Do not run it
as an offline validation step.

The AWS backup identity needs prefix-restricted `s3:ListBucket` and `s3:GetObject`
for the two immutable roots only; no AWS write/delete or mutable-state permission.
Azure needs read/list/create for the new operational blob root, using the existing
short-lived HTTPS `rcl` SAS mechanism. No overwrite/delete permission is needed.
Azure authentication/role scope must be reviewed before activation; application
path checks do not restrict an otherwise privileged credential.

## Isolated restore

Download selected archives and authenticated verified completions only after live
read approval. Independently retain the anchor and inventory/completion set needed
to establish scope; a single restored batch proves only that batch, not the whole
lineage or an atomic cutoff. With already-local files, use:

```python
from pathlib import Path
from incremental_transfer import decode, restore_batch
completion = decode(Path('batch.zip.verified.json').read_bytes())
result = restore_batch(Path('batch.zip'), completion, directory=Path('<isolated-private-directory>'))
```

The destination must be outside Git trees, private, free of symlinks/junctions and
single-writer locked. The entire archive verifies before restoration. Payloads
and original receipts are published atomically without overwrite under hashed
local filenames; exact retries succeed and conflicts fail. Test success, failure,
empty, raw-only and forecast-ledger cases. Retain the verified cutoff, gaps,
restoration duration and original location mapping. Never silently rewrite embedded
bucket/key identities or substitute reconstructed JSON for original bytes.

Restoration is isolated local recovery, not an automatic upload or active-state
resume. A later S3 upload receives new storage times; preserved receipt times do
not make it genuine historical live availability. After disaster recovery, retain
recovered predictions/proofs for audit and select a new `ForecastEpoch`, with cold
training and an explicit interruption. Do not restore head/lease or backdate labels.
Polling-control inspection/resumption is separate and must preserve source restrictions.

## Sealed historical archive

Existing historical S3 evidence and Azure artifacts remain unchanged and independent
of this anchor. In particular, retain `baseline/yata-cutoff-20261011.7z`, its
SHA-256 `e57758215de330b7f9db1fd8a75754ba2161764f32824cfb2485bcf639cef5f8`, original
coverage-catalog pin, all completion receipts and original interpretation code.
The pre-reset recovery implementation and historical operating evidence are
preserved at repository revision `f94247139184f4cc9c81daa46707a3278e8feb78`; use
that revision's backup documentation and tools for the sealed archive, not the
new operational parser.
The new tools intentionally do not reinterpret old backup schemas. Verify and retain
the frozen historical recovery tooling before replacing operational code/configuration.
No retention expiry, archive deletion, trust-root replacement or historical migration
is authorized by the reset. Current deployed backup settings continue until the
separately approved cutover.

Successful daily backups target roughly one day of potential loss, not zero loss;
failed runs extend exposure. Actual coverage, recovery duration and cloud configuration
require separately authorized measurement. See [cutover](deployment.md).
