# Torn Redeye agent guidance

## Goal

Deliver the active work package using the smallest safe, complete change.

Torn Redeye explores foreign-market stock observations, historical restock and
sellout patterns, and probabilistic forecasting. Do not expand the project into
new data sources, prediction methods, hosted services, or user interfaces unless
the approved work requires them.

The active GitHub issue defines the work package scope. For explicitly approved
bootstrap work without an issue, follow that confirmed scope instead. Accepted
decisions define durable constraints. Current code, tests, and configuration
establish implementation behavior. Do not let stale documentation or handoffs
override them.

## Default context

For normal work, read only:

- the active issue or approved bootstrap scope
- this file and the applicable `.agents/skills/*/SKILL.md` files
- affected code and focused tests, when present
- directly relevant source contracts, configuration, and documentation

Read research, decisions, and wider project history only when needed for the
specific work. Do not load unrelated documentation or historical context by
default.

## Working rules

- Implement the smallest complete change. Fix observed problems, not hypothetical
  future problems.
- Design functionality around the reusable operation, not just its first use
  case. When behavior naturally applies across items, countries, datasets, or
  similar inputs, prefer simple parameters and shared implementations over
  separate case-specific scripts, tests, and documentation. Keep genuinely unique
  behavior specific. Do not introduce abstractions, frameworks, or configuration
  solely for hypothetical future needs.
- Preserve working ingestion and persistence behavior unless the active issue
  requires a change. Do not refactor stable code solely to remove duplication.
- Do not add frameworks, abstractions, dependencies, additional providers,
  caching, concurrency, scheduling, hosting, or machine learning without a
  demonstrated current need.
- Use approved data sources and respect access, rate-limit, and platform
  constraints. Do not introduce unapproved scraping or external writes.
- Preserve raw evidence, provenance, source identity, relevant timestamps, and
  visible source failures. Distinguish missing or stale information from an
  observed zero.
- Distinguish observations from inferred events and forecasts. Do not report an
  exact restock time when the observations support only a time interval.
- Protect credentials and canonical data. Avoid destructive writes, silent
  corruption, hidden partial failures, or fabricated fallback values.
- Do not modify unrelated files or describe planned behavior as implemented.

For the MVP, safe means protecting credentials, source evidence, data integrity,
and the ability to diagnose failures. It does not mean implementing every future
production concern.

## Implementation and validation

For meaningful application, collection, persistence, or schema changes:

1. Inspect the affected behavior and source contract.
2. Implement the smallest complete solution.
3. Add or update focused tests for changed behavior and relevant failures.
4. Run focused checks while developing and any repository-required broad checks
   once after stabilization.
5. Inspect the complete affected diff and report validation evidence.

For documentation, formatting, configuration-only, or other mechanical changes,
use focused checks for the affected contract, including `git diff --check`.
Do not invent a test suite, lint command, or CI gate before one is established.
Reuse credible results rather than rerunning unchanged broad checks.

Follow the `engineering` skill for implementation, testing, security, reporting,
and bounded review details. Follow `github-workflow` for branch, issue, commit,
pull request, and finalization procedures.

Load [reporting-engineering](.agents/skills/reporting-engineering/SKILL.md)
alongside `engineering` when implementing or reviewing user-facing reports,
dashboards, charts, tables, navigation, language, or responsive presentation.
Use its project-wide read-only audit mode only when that scope is requested.
Reporting presentation and user journeys belong to `reporting-engineering`;
data processing, queries, evidence integrity, and AWS publication security remain
under `engineering`. GitHub operations remain under `github-workflow`.

## Documentation and decisions

Give each permanent document one authoritative purpose. Prefer code, tests,
configuration, and source contracts for discoverable behavior; do not copy mutable
inventories into multiple documents. Separate current status, historical
evidence, and temporary handoffs.

After implementation changes, review documentation directly affected by the
change. Update any instructions, behavior descriptions, contracts, or examples
that would otherwise become inaccurate or incomplete. Avoid unrelated
documentation changes and duplication. Identify conflicts rather than silently
reconciling them.

Record a durable decision only when it resolves a genuine architectural or
contractual question. Do not create speculative decisions or process documents.

## Review and GitHub

Self-review the complete affected diff. Require independent review when repository
guidance or a material risk requires it, not automatically for routine changes.
Use an issue and feature branch for substantive work, except an explicitly
authorized bootstrap exception.

When supplying local PowerShell commands to the user, use the canonical helpers
in `.chatgpt/powershell-helpers.ps1` for captured command evidence. They are not
requirements for application code or the agent's own execution environment.

Reuse recorded validation evidence. Do not merge unless explicitly authorized.

## Handoffs

When interrupted work cannot be reconstructed cheaply from Git and GitHub,
prepare a temporary handoff for the next session. Do not maintain a permanent
current-state handoff in the repository.
