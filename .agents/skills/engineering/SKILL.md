---
name: engineering
description: Implement or review bounded Torn Redeye work involving Python, SQL, foreign-stock ingestion, data contracts, persistence, analytics, forecasting, reports, testing, or security-sensitive changes. Apply the smallest complete solution while preserving observed evidence, uncertainty, data integrity, and reliable failure behavior.
---

# Engineering

## Responsibility

Implement or review bounded engineering work accurately and efficiently.
Preserve accepted interfaces and data contracts, assess only credible risks,
validate proportionately, and report the outcome concisely.

## Workflow

1. Read the active issue, `AGENTS.md`, and applicable repository guidance.
   If bootstrap work has an explicitly approved issue exception, use its
   confirmed scope instead.
2. Inspect the minimum relevant code, source contracts, configuration, schemas,
   tests, and accepted decisions. Expand only when the change requires it.
3. Choose the smallest complete solution that satisfies confirmed requirements.
   Preserve existing patterns unless evidence justifies changing them.
4. Implement authorized behavior and focused tests. Update authoritative
   documentation only when an accepted contract or decision changes.
5. Validate affected behavior, including relevant failures and reruns. Run
   broad checks once after stabilization only when repository guidance requires
   them. Do not rerun unchanged checks merely for reassurance.
6. Inspect the complete affected diff. Report changes, evidence, and unresolved
   risks; do not claim unperformed checks or unavailable results.

Discover repository facts before asking questions. Do not request approval for
routine inspection, focused implementation, validation, or correction of
failures introduced by the authorized change.

## Data and source correctness

- Verify affected function signatures, fields, keys, types, nullability,
  timestamps, units, paths, configuration, and downstream consumers.
- Verify provider behavior from actual source contracts or observations.
  Do not treat an undocumented assumption as an established API guarantee.
- Treat responses, files, configuration, and other external data as untrusted.
  Validate what the consumer requires, without speculative defensive layers.
- Preserve raw evidence, source identity, retrieval metadata, provenance,
  stable identifiers, and historical observations where the data contract
  requires them.
- Distinguish source-reported values, collection timestamps, inferred events,
  and forecasts. Never present an event as occurring at an exact observation
  timestamp when the evidence only bounds when it could have occurred.
- Preserve material uncertainty, collection gaps, source freshness, and the
  distinction between unavailable data and an observed zero.
- Use explicit UTC handling and preserve source timezone context where relevant.
  Do not invent time precision unsupported by the observations.
- Design writes and orchestration for safe reruns when required: check
  idempotency, transaction boundaries, duplicate processing, partial results,
  failure isolation, and recovery against the confirmed behavior.
- Trace failure requirements end to end. Distinguish invalid application
  configuration from invalid source results. Where evidence retention or
  independent continuation is required, do not reject all work prematurely.
  Cover affected entry points with tests of the actual failure path.
- Preserve meaningful exception context; do not silently discard failures,
  substitute values, or add fallback behavior without authorization.

## Implementation and tests

- Keep responsibilities and control flow clear. Add abstraction only for
  demonstrated duplication, variation, isolation, or scale.
- Prefer the standard library and existing dependencies. Justify additions by
  concrete value relative to maintenance and security cost.
- Consider retries, batching, memory, network calls, database round trips,
  concurrency, caching, or distributed execution only when observed behavior
  or expected scale requires them.
- Test meaningful outcomes, including applicable boundary, mismatch,
  failure, rerun, and partial-result cases. Do not write tests that merely
  execute code without checking behavior.
- Keep SQL keywords and non-case-sensitive identifiers lowercase.
- For short Python comments, use `#`, lowercase wording, and no terminal
  period on a single-line comment. Avoid comments that merely restate code.
  Use triple-quoted blocks for docstrings or genuinely large comments.

## Security-sensitive work

When a change crosses a security or destructive-state boundary, inspect the
relevant assets, identities, permissions, inputs, and concrete failure paths.
Separate demonstrated vulnerabilities from optional hardening.

- Prevent untrusted values from reaching commands, file paths, queries,
  deserialization, or privileged operations without appropriate validation.
- Use the narrowest practical token, identity, workflow, and resource access.
  Never hardcode or log credentials, API keys, connection strings, or secrets.
- Do not weaken authentication, authorization, transport security, or failure
  behavior for convenience.
- Assess new dependencies and third-party workflow code as supply-chain inputs.
- Protect canonical state from accidental overwrite, conflicting writers,
  unsafe fallbacks, partial publication, and unintended deletion.
- Require explicit authorization for destructive operations or privilege
  expansion. A finding is blocking only when evidence supports a credible
  exploit or failure path, not a hypothetical hardening preference.
- Do not turn routine work into a repository-wide security audit.

## Reporting and forecasting outputs

Apply these rules when implementing or reviewing user-facing outputs, exports,
charts, reports, or interactive surfaces; do not require a dashboard where none
has been approved.

- Present observed, derived, predicted, missing, failed, and unknown values
  distinctly. Explain freshness, provenance, and uncertainty where they affect
  interpretation or a user's decision.
- Use understandable labels, units, and clear information hierarchy. Do not
  expose internal field names as default product wording or display every
  metric simply because it exists.
- Verify output against source data and the intended user action. For rendered
  interfaces, also check relevant controls, routes, error states, keyboard
  access, and wide/narrow layouts.
- When browser behavior is changed, validate the built artifact and the
  user-visible state actually claimed. Do not use arbitrary sleeps to conceal
  reactive timing problems. Check deployed output only when in scope.
- Separate functional defects and misleading claims from optional visual polish.

## Bounded read-only review

When explicitly asked to review, or when a required independent review is
performed, do not edit files, mutate GitHub, or run live pipelines.

- Establish the exact target issue or approved scope and revision. Include all
  staged, unstaged, and untracked changes when reviewing a working tree; use the
  immutable base-to-head diff for revision review.
- Map requirements and explicit exclusions to the affected implementation,
  tests, configuration, and relevant authoritative documentation.
- Assess recorded validation evidence before repeating checks. Distinguish
  confirmed defects, nonblocking improvements, and missing evidence.
- Support each finding with precise evidence, demonstrated impact, and the
  smallest effective correction. Do not promote preferences to requirements.
- For documentation review, check authority, duplication, stale status,
  speculative decisions, mutable copied lists, and maintenance drift. Preserve
  distinct decisions and contracts; prefer references over duplicated content.
- Keep reviews bounded. Do not create review artifacts or audit ceremonies for
  routine low-risk changes unless required by repository governance.

## Human decision gates

Ask only when an unresolved choice materially affects accepted behavior or
scope, durable architecture or interfaces, data integrity, security,
permissions, destructive actions, or external writes beyond authorization.
State the evidence and smallest viable options; do not silently choose.

## Boundaries and completion

Do not introduce unrelated refactoring, new frameworks, service layers,
parallelism, strict typing tools, coverage targets, dashboards, or compliance
processes without demonstrated need. Do not absorb unrelated pre-existing
failures into the work package. GitHub lifecycle procedures belong to the
`github-workflow` skill.

Report only the result, changed or reviewed paths, substantive behavior or
findings, validation evidence, external actions, and outstanding risks or
blocked checks. Keep the report concise.
