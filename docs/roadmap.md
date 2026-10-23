# Product roadmap

This is long-term product direction, not approved implementation commitments or
a schedule. Active GitHub issues define bounded work. See the [README](../README.md)
for implemented capabilities and the linked contracts for experimental work.

## Product direction

- **Continuously updated all-country stock intelligence.** Use shared country/item
  processing to show availability, source freshness, gaps and uncertainty across
  supported overseas shops.
- **Historical stock and price trends.** Explain how reported quantities and
  prices change over time while preserving source provenance and unknown periods.
- **Probabilistic availability and restock forecasting.** Develop reusable methods
  across country/item pairs and, where evidence supports it, multiple replenishment
  cycles. Distinguish source-reported availability from inferred event intervals.
- **Transparent prospective forecast accuracy.** Preserve predictions before their
  targets and report subsequent errors, coverage, exclusions and uncertainty.
  Experimental forecasts must not imply independently demonstrated usefulness.
- **Arrival-aware purchasing and travel recommendations.** Relate uncertain stock
  at arrival to purchase options rather than assuming today's stock will persist.
- **Travel optimization.** Compare expected profit, travel time and player
  constraints, making assumptions and uncertainty visible.
- **Adaptive multi-trip planning.** Propose revised plans when missed flights,
  unexpected stock changes or other disruptions invalidate an existing plan.
- **Progressive personalization and useful alerts.** Adapt to player preferences
  and constraints and surface actionable changes with understandable reasoning.

Statistical baselines come before more complex methods. Machine learning remains
an optional future improvement requiring evidence of useful gains. Hawaii Shark
Fin is the initial research case, not a permanent product or architectural limit.
The implemented private shadow consumer is experimental; broader forecasting,
recommendations, travel planning, personalization and alerts remain planned.

### Adaptive forecasting direction

Develop one reusable forecasting framework across country/item pairs, with
independently learned behavior and uncertainty for each pair. Learn from sellouts,
replenishment candidates, quantity changes, irregular cycles and changing behavior
over time. Do not impose a universal restock duration, prediction horizon or
forecast update frequency. Future outputs should support uncertain replenishment
windows and availability at user-requested times, including travel arrival, where
the evidence supports those targets.
Player location or travel status affects the requested time, not the underlying
forecasting approach: players traveling and players already abroad share the same
country/item replenishment and depletion estimates.

Progress toward near-real-time processing and forecast updates within YATA's
known, accepted user-reported source latency. This is a product goal, not a
measured latency guarantee or an authorization to increase source requests.
Preserve provenance, missingness and source freshness; distinguish source and
retrieval times from uncertain event intervals. More frequent processing cannot
recover events hidden by delayed or cached source reports.

Start with simple evidence-supported methods and compare them prospectively
against feasible baselines before adding complexity. Learn only from information
and resolved outcomes available at each decision; assess changing behavior without
rewriting earlier predictions. No ML method, service or software framework is
prescribed by this direction.

Hawaii Shark Fin and Japan Xanax are contrasting research cases for shared methods,
not separate item-specific systems. The frozen Hawaii Shark Fin 60-minute benchmark
and completed private pilot implementation remain bounded experiments. Their hourly
cadence, horizon and freshness rules do not set universal product standards; their
contracts and historical results remain unchanged. Live pilot execution does not
establish validated forecasts or public purchasing/travel advice.

## Delivery milestones

TornRedeye advances toward useful, evidence-backed stock availability forecasts through delivery milestones, not fixed dates or arbitrary sample-size thresholds. Hawaii Shark Fin is the initial research case, not a permanent architectural boundary. Preserve evidence continuity and backup through separately authorized operational changes.

1. **Evidence foundation.** Collect, preserve, verify, and prepare historical stock observations. **Exit:** Traceable evidence and reusable stock-history processing are available.

2. **Forecasting target selection.** Determine which outcomes the observations can credibly predict, including limitations of inferred replenishment events. **Exit:** A defined prediction target, forecast horizons, and a defensible evaluation approach.

3. **Offline forecasting baseline.** Produce initial probabilistic forecasts for Hawaii Shark Fin and compare them against feasible simple alternatives. **Exit:** Reproducible predictions, historical evaluation, and clearly reported uncertainty. Production accuracy is not assumed.

4. **Forward validation.** Evaluate a selected approach against subsequently collected observations that did not inform its selection. Examine forecast reliability, calibration, and performance against simple baselines. **Exit:** Evidence supporting practical usefulness, or a documented reason to revise the target or method.

5. **Useful forecasting MVP.** Make sufficiently supported forecasts available through the existing reporting system, including probability, horizon, evidence freshness, and uncertainty. **Exit:** Users can interpret predictions to inform stock-purchasing or travel decisions, with insufficient-evidence outcomes where necessary.

6. **Generalization and improvement.** Extend shared forecasting methods across supported country/item pairs and multiple future replenishment cycles. Introduce more sophisticated methods only when justified by evidence. **Exit:** Broader coverage with measured reliability, without separate hardcoded forecasting systems for individual items.

### Progression principles

- Substantive research should resolve a decision affecting the next deliverable. Do not repeatedly analyze the same exploratory sample without a consequential question.
- Build and measure simple baselines early. Distinguish exploratory historical findings from independently validated forecasting performance.
- Advance based on demonstrated milestone outcomes, not calendar age or raw observation counts. New evidence may justify revisiting earlier decisions.
- Prioritize reusable country/item forecasting capabilities over special-purpose solutions or speculative infrastructure.
- Preserve uncertainty and explicitly report insufficient evidence. Never present inferred events or unvalidated forecasts as established facts.
- Treat milestones as product direction, not a backlog of issues to open in advance. Active GitHub issues define bounded work packages.
