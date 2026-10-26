# Local stock history

`tornredeye.stock_history` reads a caller-selected local subset of retained source
exports for an explicitly selected country and item. It prints a deterministic
JSON report to standard output. It never changes evidence, contacts a cloud
service, or downloads history. Use Python 3.11 or newer.

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

For example, select Hawaii Shark Fin (`haw`, item ID `1485`) from the repository
root in PowerShell:

```powershell
. ./.chatgpt/powershell-helpers.ps1
Invoke-NativeCommand python @('-B', '-m', 'tornredeye.stock_history', 'C:\private\selected-subset', '--country', 'haw', '--item-id', '1485')
```

`--country` and `--item-id` are required; use the exact YATA country key and a
positive integer item ID. For example, `--country jap --item-id 206` selects
Japan Xanax from the same export format. Each report identifies the selected
country/item ID; names remain source-reported on each observation. The Python API
signature is `analyze(directory, *, country, item_id)`: both selection arguments
are required keyword-only arguments, with no country/item defaults. `--help`
describes the CLI arguments.
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

## Observed stock-cycle summaries

The same command also returns `stock_cycles`, derived from the existing validated
observations and adjacent comparisons. No second evidence pipeline, freshness
threshold, assumed polling coverage or item-specific timing rule is used.

- `comparison_counts` counts increases, decreases and unchanged quantities only
  when the existing comparison is eligible. `candidates` selects zero-to-positive
  and positive-to-zero changes, retaining both receipt references, the net quantity
  change, source-update interval and retrieval interval. `candidate_counts` counts
  these observed transitions, **not actual restocks or sellouts**. An increase
  between positive reports remains ambiguous, not a replenishment event.
- `appearance_intervals` links consecutive zero-to-positive candidates only within
  an uninterrupted sequence of usable rows. For source-update brackets `(a, b]`
  and `(c, d]`, `source_change_separation_bounds_seconds` is `[max(0, c-b), d-a]`;
  `appearance_report_separation_seconds` is `d-b`. These describe the separation
  of candidate source-report changes, conditional on those brackets. They are not
  measured game-event intervals, independent cycle samples or a restock distribution.
  Cached receipts cannot narrow the source brackets. Unknown coverage and hidden
  events remain possible even without an explicit invalid row.
- `positive_report_runs` groups consecutive usable positive reports. Each run
  retains its first/last receipt, source and retrieval endpoints, first/last quantity,
  distinct source-report count, cached-receipt count and `source_span_seconds`.
  The latter measures the span of observed positive source reports, **not a lower
  bound on continuous availability or a stock lifetime**. Cached receipts extend
  the retrieval endpoint but not the source span or distinct source-report count.
  Opening/closing candidate receipt references identify observed boundary changes;
  `subset_start`, `subset_end` and `quality_barrier` boundaries mark incomplete
  windows rather than inventing missing appearances or disappearances.
- Within a positive run, `net_increase_units` and `net_decrease_units` sum the
  positive and negative adjacent net changes separately; `unchanged_comparisons`
  counts advancing-source reports with unchanged quantity. Opening/closing changes
  are in the referenced candidates, not these within-run totals. These summaries
  describe reported depletion and additions, not sales volumes, consumption rates
  or confirmed replenishment amounts. Simultaneous purchases and sellbacks can
  cancel in a net change; an unchanged report does not prove inactivity.
- `issue_counts`, usable-observation and cached-receipt counts, the usable-source
  age range and maximum retrieval spacing summarize evidence quality. Cached and
  issue counts can overlap. Every sampling interval has unknown coverage; no
  threshold silently turns a long interval into a complete observation window.
  Invalid/missing rows break runs and candidate links. Unplaced evidence suppresses
  all such linkage. With no eligible comparisons, status is
  `insufficient_evidence_for_transitions`; otherwise it is `snapshot_comparisons_only`.
  A bodyless collection attempt cannot be discovered in this selected raw subset;
  absence of a row is not proof that collection or stock availability was continuous.

Source-update brackets express changes between reported source states. Retrieval
times describe when TornRedeye obtained them, not when a player could buy stock;
source age is retained without an invented cutoff. Existing source conflicts are
flagged across the supplied history retrospectively. This descriptive report is
not an as-of forecasting decision replay and must not supply future-known flags
to a historical forecast. Short-lived events, multiple changes between snapshots,
sellbacks, source latency and missing evidence limit interpretation. The
[Torn travel reference](https://wiki.torn.com/wiki/Travel) documents that players
can bring items abroad and sell them; quantity increases alone cannot identify
their cause.

For example, Hawaii Shark Fin (`--country haw --item-id 1485`) and Japan Xanax
(`--country jap --item-id 206`) use the same analysis. To compare them, run each
against the same explicitly selected, verified export subset and retain its
identities, endpoints, quality diagnostics and private JSON outputs to reproduce
a comparison. One day cannot establish a reliable long-term replenishment
distribution. The October 21,
2026 Traveling 2.0 changes are possible context for changing purchasing/depletion
patterns, not evidence of causation. No regime-change model or forecasting method
is fitted here. Evaluate any later timing/depletion method on subsequently
available evidence with unresolved outcomes left explicit.

## Offline checks

```text
python -B -m unittest discover -s tests -p test_stock_history.py
python -B -m unittest discover -s tests
git diff --check
```
