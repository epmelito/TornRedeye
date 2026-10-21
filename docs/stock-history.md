# Local stock history

`tornredeye.stock_history` reads a caller-selected local subset of retained source
exports for a selected country and item. Hawaii Shark Fin (`haw`, item ID `1485`)
is the primary example. It prints a deterministic JSON report
to standard output. It never changes evidence, contacts a cloud service, or
downloads history. Use Python 3.11 or newer.

## Input and command

Use the existing [local backup receipt/payload layout](evidence-backup.md#isolated-restore):

```text
selected-subset/
  objects/
    <SHA-256-of-original-S3-key>.json
    <same-hash>.bin
```

Select the original raw receipts and matching payloads locally; do not rename
them, manufacture metadata, or overwrite originals. A full backup manifest is
not required for this selected subset. Receipts retain the existing canonical
`{value, sha256}` wrapper and must have `status: verified`. Their identity,
inventory metadata, payload length and hash are checked with existing backup
helpers. Normalized and forecast receipts are ignored by this raw-subset analysis.
The active reader accepts only the fresh time-partitioned evidence contract.
For sealed historical evidence, use the preserved archive interpretation code;
these operational tools do not translate old identities. Raw objects contain the
complete source export; country/item selection is independent of storage naming.

From the repository root in PowerShell:

```powershell
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand python @('-B', '-m', 'tornredeye.stock_history', 'C:\private\selected-subset', '--country', 'haw', '--item-id', '1485')
```

`--country` and `--item-id` are required; use the exact YATA country key and a
positive integer item ID. For example, `--country jap --item-id 206` selects
Japan Xanax from the same export format. Each report identifies the selected
country/item ID; names remain source-reported on each observation. The Python API
is `analyze(directory, country="haw", item_id=1485)`. `--help` describes the arguments.
Input contains private evidence identities and
metadata; the output retains them and is a local report, not public dashboard data.
Exit 0 means a report was produced, including any explicit row issues/warnings;
exit 2 means the input directory could not provide a report. No live evidence
analysis or cloud access is authorized by this command.

## Reading the report

- `observations` retain every selected raw receipt, ordered by original UTC
  retrieval time. Each row includes quantity, name, cost, source/export timestamps,
  source/export ages at retrieval, collection context, raw provenance, original
  retrieval metadata, and all-stock normalization diagnostics. Unplaceable rows
  follow chronological rows. Missing quantities remain null, distinct from zero.
- `comparison` describes an observed increase, decrease or unchanged quantity
  between adjacent usable snapshots with advancing source-update times.
  `zero_to_positive` flags the observed zero-to-positive case. Retrieval intervals,
  source-update intervals and elapsed seconds are separate. These are comparisons
  of reported snapshots, not exact restock/sellout times or counts of stock events.
- Repeated source timestamps/quantities remain visible and are marked
  `repeated_source_observation`, without generating new changes. Old source data
  retains its measured age; there is no invented stale/fresh cutoff. Cached
  retrieval intervals do not bound actual stock events: a newly retrieved value
  can describe an earlier source update.
- Missing/invalid HTTP status never defaults to 200. Failed/unknown collection
  context, invalid targets, unknown/future timestamps, regressing source time,
  conflicting quantities at one source time and simultaneous retrievals block
  adjacent comparisons. Unrelated malformed records do not discard a valid target.
  Original country update zero means unknown time, not Unix epoch.
- Invalid or missing observations are never bridged. If evidence cannot be placed
  in time (including corrupt receipts/payloads or orphan payloads), a report warning
  suppresses all comparisons. A long gap between valid endpoints reports elapsed
  time; it does not establish continuous coverage or exclude intervening changes.

The report covers only the supplied subset, not the entire archive or all
collection attempts. Reuse requires original retrieval metadata, not file mtime,
S3 LastModified or processing time. The tool reuses `tornredeye/normalization.py`; it does
not alter the collector, normalizer, persistence, backup or dashboard contracts.

## Offline checks

```text
python -B -m unittest discover -s tests -p test_stock_history.py
python -B -m unittest discover -s tests
git diff --check
```
