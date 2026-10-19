---
name: data-science
description: Design or review empirical research, statistical inference, forecasting methods, and model evaluation when scientific validity depends on assumptions, bias, uncertainty, or validation design. Exclude routine parsing, ETL, exact aggregation, presentation-only edits, and software correctness checks.
---

# Data science

Assess what evidence supports and how to test analytical claims. Apply this skill
to substantive methodological choices, including analyses without models or code.
Software correctness, pipelines, persistence, security and deployment belong to
applicable engineering guidance; combine it with this skill when implementing
analytical methods. Do not expand a mechanical data task into a research project.

## Frame the question

- Identify the decision or claim, target quantity, unit of analysis, population
  and relevant horizon. Distinguish description, prediction and causal estimation;
  success at one does not establish the others.
- Check whether the available observations identify the target. If several latent
  processes could produce the same measurements, limit the claim or propose the
  smallest discriminating analysis rather than forcing a model or point estimate.

## Assess the evidence

- Trace how units enter the sample and how measurements become available. Assess
  selection, missingness, censoring, measurement error and changing definitions
  where they could change the conclusion. Do not assume missingness is random.
- Identify independent experimental or sampling units. Repeated measurements,
  shared sources and clustered observations do not automatically increase the
  effective sample size. Match uncertainty estimation and validation to that
  dependence; do not prescribe an IID test or resampling scheme by default.
- Consider plausible competing explanations for the pattern, such as composition
  changes, measurement changes or confounding. For causal claims, state the design
  and identification assumptions; predictive association alone is insufficient.

## Choose and challenge methods

- Match the method to the target, observation process, data support and assumptions.
  Use credible simple baselines for model comparisons. Add complexity only when
  evaluation demonstrates relevant benefit; do not require a named model or ML approach.
- Choose evaluation criteria from the intended use, including asymmetric errors
  where relevant. Separate exploratory discovery from confirmatory evidence;
  repeated searches, subgroup selection and multiple comparisons affect how
  strongly a finding can be interpreted.
- Separate method selection from final assessment. Keep transformations, feature
  selection and tuning inside training partitions. Choose splits that represent
  the intended generalization across units, groups or time; avoid leakage and
  repeated adaptation to a supposedly untouched test set.
- Check whether apparent gains survive plausible alternative assumptions, data
  exclusions or baselines. Use diagnostics that could falsify the proposed account;
  report contradictory evidence rather than selecting only favorable results.

For forecasts, temporally dependent analyses, or event timing inferred from
intermittent observations, read [temporal evaluation](references/temporal-evaluation.md).
Do not load it for ordinary analyses without those issues.

## Express uncertainty and reproducibility

- Distinguish variation in outcomes, uncertainty in estimated quantities, and
  uncertainty from measurement or modeling assumptions. Explain what an interval
  or probability covers and what it omits. Statistical significance is not effect
  size, practical usefulness, or certainty; lack of significance is not equivalence.
- For probabilistic predictions, assess calibration and sharpness together with
  an appropriate proper scoring rule when feasible. Interval coverage alone can
  reward uninformatively wide intervals. Qualify subgroup findings when evidence
  is sparse, and assess whether evaluation conditions support the intended use.
- Retain enough information to reproduce the conclusion: data identity and cutoff,
  exclusions, transformations, split definitions, method settings and randomness
  where used. Scale this record to the work; do not invent a tracking platform.
- Report the supported conclusion, assumptions that matter, evaluation evidence
  and material limitations. If evidence cannot resolve the question, say so and
  identify what observation or comparison would resolve it. Do not replace
  unavailable evidence with precision or a stronger causal interpretation.
