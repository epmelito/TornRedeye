# Temporal evaluation

Use this reference for forecasting, temporal dependence, and event timing from
intermittent measurements. Choose the relevant guidance rather than imposing a
fixed model, sampling interval, holdout duration or retraining schedule.

## Reconstruct the information available

Distinguish event or source time from measurement availability, retrieval and later
revision. At each evaluation origin, use only inputs actually available then;
joining on event time alone can leak delayed reports or revised historical values.
Likewise, training labels must have become observable by that origin. Define how
unresolved or censored outcomes contribute rather than labeling them as negatives.

Repeated cached measurements may describe one underlying state, not independent
new events. Irregular sampling can overrepresent frequently observed states.
Decide whether the target weights observations, elapsed time, entities or events
before estimating rates or durations. Distinguish an event known only within an
observation interval from an exact event time; treating an imputed point as observed
can distort estimated cycles or lead times.

Evaluate interval-censored outcomes with methods that account for the observed
intervals and the censoring process under explicit assumptions, such as an
appropriate censored-data likelihood. Midpoint errors and interval-overlap checks
are diagnostics only: they do not establish exact-event accuracy or probability
calibration. Claims about either require evaluation that supports that target
despite censoring.

## Evaluate the actual forecasting task

Use chronological or rolling-origin evaluation when estimating future performance.
Replicate the intended training window, update policy, forecast horizons and
availability of future covariates. Known calendar inputs differ from future
measurements that would themselves need forecasting. Fit preprocessing and tune
methods using only eligible training/validation information at each origin.

When labels or feature windows overlap, inspect whether a later partition reveals
outcomes unavailable to an earlier origin. Separate affected windows or justify
their admissibility from actual information availability; do not apply an arbitrary
gap as a universal cure. Keep a final assessment separate from choices informed by
backtest results. An adaptive update must not consume an outcome before it resolves.

Evaluate multiple-step predictions at the requested horizons without supplying
intermediate realized outcomes that would not yet be known. For recursive methods,
propagate predicted-state uncertainty rather than treating each predicted input as
observed truth. Compare against feasible simple or seasonal baselines under the
same origins, horizons and information constraints.

## Interpret dependent and changing evidence

Report errors and probability calibration by horizon and by relevant entity or
period when support permits; an aggregate gain can hide poor performance on the
intended subgroup. Overlapping forecasts and serially dependent errors cannot be
treated as independent evidence: account for dependence when estimating uncertainty in
performance differences. Resampling blocks or clustering can help when their
assumptions fit; no fixed block length works for every process.

Check whether changes in sampling, missingness or source age explain apparent
seasonality or drift before interpreting them as process changes. State which
regimes the backtest covers. Performance under observed conditions does not
establish reliability after a structural change or under unobserved conditions.
