# TornRedeye

TornRedeye explores foreign-market stock observations in Torn, an online game.
It preserves overseas shop evidence to study availability, restock intervals and
sellout patterns, with probabilistic forecasting as an experimental capability.
It uses the existing YATA export source; missing or stale reports never imply
observed zero stock.

## How it works

A one-minute AWS Scheduler invokes a guarded Lambda collector. Each retrieval
retains its complete raw export and a versioned all-country/item evidence record in
private S3 storage. CloudWatch/SNS monitor collection, and GitHub Actions copies
verified canonical evidence to Azure. A separate publisher serves the public
operations dashboard.

Shared Python normalization reads every returned country and item from retained
exports. Local tools analyze historical observations; an independent, private
shadow consumer records prospective availability forecasts and outcomes when
separately deployed and activated.

## Capabilities and limitations

- **Implemented operational foundation:** guarded collection, immutable evidence
  writes, integrity-checked incremental backups and an operations dashboard.
- **Implemented shared processing:** deterministic all-country/item normalization,
  field diagnostics and reusable local history analysis. The clean reset uses a
  fresh, time-partitioned lineage with bounded prospective reads and independent
  Azure recovery. These local changes await separately authorized cutover; old
  evidence remains a sealed archive.
- **Experimental forecasting:** offline empirical/persistence comparison and a
  private 60-minute Hawaii Shark Fin shadow consumer. Its AWS definition defaults
  to disabled. Predictions are not validated purchasing or travel advice.
- **Planned product capabilities:** broader stock intelligence, historical trends,
  forecasting and travel support are described in the [roadmap](docs/roadmap.md).
  They are not implementation commitments.

## Getting started

Use Python 3.11 or newer, from the repository root:

```sh
python -B -m unittest discover -s tests
python -B -m tornredeye.stock_history --help
python -B -m tornredeye.availability_forecast --help
```

Tests use synthetic evidence and fake cloud clients; no cloud credentials are
needed. Local analysis requires an explicitly selected private evidence subset.
Deployment and any live source/storage access are separate operating actions.

## Repository layout

```text
tornredeye/    Shared normalization, stock history and experimental accounting
tools/         Lambda package builders and daily backup wrapper
tests/         Deterministic tests and fixtures
docs/          Contracts, operation instructions and product roadmap
dashboard/     Existing static operations dashboard
```

Operational Lambda entry points, their supporting collector/backup modules and
AWS templates remain at the root to preserve deployed handlers and operating
commands. Package builders include the shared package where required. No package
installation or additional framework is required.

## Documentation

- [Shared stock normalization and integration boundary](docs/stock-normalization.md)
- [Local stock history](docs/stock-history.md)
- [Offline availability baseline](docs/availability-forecast.md)
- [Private shadow forecasting](docs/shadow-forecast.md)
- [Deployment and operation](docs/deployment.md)
- [Monitoring and resource use](docs/monitoring.md)
- [Backup, verification and recovery](docs/evidence-backup.md)
- [Public operations dashboard](docs/dashboard.md)
- [Long-term product roadmap](docs/roadmap.md)
