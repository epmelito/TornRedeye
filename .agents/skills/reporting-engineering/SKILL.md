---
name: reporting-engineering
description: Implement or review Torn Redeye reporting presentation, user journeys, navigation, language, charts, tables, responsive layouts, browser behavior, and accessibility basics. Use for bounded reporting implementation, bounded read-only review, or explicitly requested project-wide read-only reporting audits.
---

# Reporting engineering

## Purpose and ownership

Treat reporting as product design, not data serialization. Correct data alone
is insufficient without hierarchy, understandable language, purposeful flow,
and a usable presentation.

Apply this skill to collection operations, infrastructure consumption, stock
observations, historical trends, uncertainty, and approved future restock
forecasting. It governs how users understand those outputs; it does not authorize
new analytics, predictions, data sources, or reporting surfaces.

Read the active issue, [AGENTS.md](../../../AGENTS.md), and the minimum relevant
contracts and implementation. Use [engineering](../engineering/SKILL.md) for
Python data processing, queries, evidence integrity, testing, and AWS publication
security. Use [github-workflow](../github-workflow/SKILL.md) for GitHub operations.
This skill adds presentation and user-journey standards to those responsibilities,
not a separate approval or finalization process.

For approved visual exploration or redesign, also load
[Anthropic Frontend Design](../frontend-design/SKILL.md). For a UI compliance
review, load [Vercel Web Design Guidelines](../web-design-guidelines/SKILL.md),
which includes pinned local rules. Impeccable's instruction-only
[Operate guidance](references/impeccable/operate.md) adds product-interface depth;
read its [craft floor](references/impeccable/craft-floor.md) after direction is
settled and before UI edits. These are references, not an installed Impeccable
command skill; no engine, hooks or subagents are available from this setup.
See [external design provenance](../EXTERNAL-DESIGN.md) for versions and limits.

External guidance complements this skill and `engineering`; it does not expand
approved scope or override the active issue, user decisions, evidence contracts,
accessibility or repository requirements. Treat upstream aesthetic rules and
framework/library examples as guidance, not authority to change the stack, add
dependencies, invent product content or run tools. Preserve explicit brand
choices and timestamp semantics. Resolve conflicting external preferences against
the approved brief and local requirements; report material unresolved conflicts.

For operations reporting, consult [dashboard.md](../../../docs/dashboard.md) for
the current dashboard contract and [monitoring.md](../../../docs/monitoring.md)
for signal and consumption semantics when relevant. For other reporting, use
the affected code, tests, source contracts, and accepted decisions. Do not treat
an operations-specific document as a universal analytics requirement.

Choose the requested mode:

1. **Bounded implementation:** change one approved reporting outcome.
2. **Bounded read-only review:** assess one reporting surface or change.
3. **Project-wide read-only audit:** assess the agreed maintained reporting system.

Read-only modes must not modify repository files, generated artifacts, GitHub
state, or deployed content. Bounded work must not become an unrelated redesign.

## Product standard and reporting flow

Design for the intended audience and the questions they need answered. Public
operations reporting should be understandable without private operator knowledge.
Every visible element should support the page purpose, comprehension, an intended
next step, or necessary context, evidence, uncertainty, or provenance. Do not add
controls or display every available metric merely because they are possible.

Review the connected user journey, not just isolated pages:

- Make the entry point explain what the report provides and its limits.
- Order sections around the user's likely questions and make primary information
  or destinations easy to find.
- Separate current operations, observations, historical evidence, forecasts,
  and deeper technical explanations where their purposes differ.
- Provide clear onward and return paths where multiple surfaces exist; direct
  links and refreshed routes should retain understandable context.
- Prioritize relevant recent information over growing archives. Use grouping,
  condensation, relocation, or progressive disclosure to control density.

Avoid arbitrary card grids, endless tables, repeated information, equal emphasis
for every metric, unexplained technical labels, and decoration without meaning.
Use deliberate typography, whitespace, alignment, grouping, and restrained color
to establish hierarchy. Do not turn a single-page report into a multi-page site
without an approved need.

When adding or materially restructuring a maintained page, compare representative
sibling pages and shared layout rules for content width, left edges, control
alignment, and responsive behavior. Prefer consistent outer layout unless the
page purpose warrants a deliberate difference. Do not introduce a framework or
shared component abstraction solely to enforce visual uniformity.

## Language and data meaning

Use natural everyday wording. Translate internal fields, enums, pipeline terms,
and academic phrasing when a clear user-facing alternative exists. Explain units,
UTC timestamps, time windows, provenance, and coverage where interpretation
depends on them; keep implementation details out of the default experience.

Preserve distinctions between measured observations, derived values, estimates,
forecasts, unavailable, unknown, stale, failed, empty, and not applicable data.
An observed zero is a value; unavailable data is not zero. Labels must match the
displayed value's actual classification, including when that value is absent.
Keep publication age, retrieval freshness, and provider-source freshness separate
where the reporting contract distinguishes them.

Do not simplify wording into unsupported claims about collection success,
complete coverage, exact distinct counts, billed costs, remaining allowances,
restock or sellout times, or prediction certainty. Show intervals and uncertainty
when the evidence supports them rather than precise events or guaranteed outcomes.
Engineering owns the calculations and evidence contract; presentation must
communicate their limits faithfully.

## Visual, functional, and accessibility checks

Assess composition, section sequence, visual hierarchy, density, alignment,
navigation prominence, and consistency. Choose charts for relationships or trends
and tables for precise values or comparisons; omit a chart when it adds no useful
understanding. Check labels, scales, legends, units, readability, missing periods,
and supporting context. Secondary detail should remain discoverable without
dominating the default view.

Verify the reporting purpose against rendered behavior:

- Correct values, formatting, classifications, units, and meaningful defaults.
- Controls and dependent content, direct routes, refresh, cross-page links, and
  handoffs under the actual site base path, where those features exist.
- Readable charts and tables, usable overflow, and appropriately restructured
  desktop and mobile layouts rather than a compressed desktop view.
- Loading, unavailable, partial, stale, empty, and failure states, with no false
  reassurance or concealed uncertainty.
- Agreement between source data, generated output, and rendered presentation;
  no runtime errors, unresolved modules, leaked expressions, or private identifiers.

Check keyboard-operable controls, visible focus, meaningful control labels,
logical headings, and sufficient contrast for readability and meaning. Do not
rely on color alone to communicate a state. This is a functional accessibility
baseline, not an exhaustive audit.

## Bounded implementation

1. Identify the approved reporting contract, user outcome, and affected journey.
2. Inspect the minimum relevant data, source, tests, and generated output. Inspect
   sibling pages and shared layout only when adding or restructuring a page.
3. Preserve public contracts and routes unless the issue changes them. Keep query
   and export logic outside browser presentation unless an accepted design requires
   otherwise; apply engineering's data and publication safeguards.
4. Implement the smallest complete change. Use focused correctness fixtures and,
   when scale affects density or layout, representative sanitized data for visual
   verification. Do not alter canonical evidence to create test data.
5. Build the affected production artifact when a build exists; for plain static
   assets, serve those assets. Inspect actual rendered output and exercise changed
   controls, routes, states, wide/narrow layouts, and accessibility basics.
6. Run focused checks required by engineering and inspect the complete affected
   diff. For documentation-only work, use documentation checks, not application
   test reruns.

Browser checks must wait for the exact user-visible state being asserted. Do not
use sleeps or unrelated readiness signals to hide timing defects. Small fixtures
alone do not establish visual quality when chart density, table growth, or history
length matters; record the scale, states, and viewports actually inspected.

Inspect deployed output only when in scope and authorized. If it differs from
local output, compare source, built artifact, and deployed result before assigning
the defect. Do not assume repository changes are live. If browser or artifact
verification is unavailable, report the missing evidence instead of claiming it
passed. Restore any temporary fixture changes before final validation.

## Bounded read-only review

Establish the exact issue/scope and revision using engineering's review guidance.
Inspect affected source, available generated artifacts, browser behavior, and
deployed pages only as needed and within scope. Assess both functional correctness
and presentation quality against the intended user outcome.

Use recorded validation evidence before repeating checks. Do not rebuild or
overwrite artifacts in read-only mode. Distinguish supported defects, optional
improvements, and missing evidence. Provide reproducible findings; broaden the
review only when a related defect prevents a valid conclusion.

## Project-wide read-only audit

Use this mode only for an explicitly requested audit. Define included maintained
surfaces, representative routes and states, wide/narrow viewports, environments
(existing generated, local, or deployed output), and exclusions before assessing.

Assess entry points, static reports, dashboards, deep links, navigation, growing
historical views, language, information architecture, charts, tables, controls,
responsive behavior, and unavailable states across that scope. Representative
coverage can establish shared maintained behavior without inspecting every archive.

Describe the target reporting experience as well as defects. Identify content
to remove, condense, group, relocate, or separate. Turn supported findings into
bounded follow-up recommendations, not one giant cleanup or automatic issue
creation. Do not implement changes during the audit.

## Findings and severity

Each finding needs severity (blocking/nonblocking), classification, reproducible
evidence, affected surface or files, the problem, its user impact, and the smallest
effective correction. Evidence may include exact routes, viewport conditions,
controls, screenshots, rendered values, source locations, or observed errors.
Use `missing evidence` when a conclusion cannot be verified; personal visual
preference is not a defect.

Useful classifications include correctness, language, navigation, information
architecture, reporting flow, visual hierarchy/density, responsive overflow,
accessibility, missing evidence, and optional polish.

Block when a finding materially prevents understanding or intended use: misleading
values or certainty; hidden primary information; broken controls, routes, or
navigation; runtime failures; unusable overflow; inaccessible interaction; or
missing state distinctions, labels, focus, or contrast that affect meaning or use.
Minor spacing, styling, and polish are nonblocking unless their impact establishes
otherwise. Missing evidence limits the conclusion; it does not prove a defect.

## Boundaries and completion

Do not require a design system, component library, screenshot regression system,
multiple browser engines, exhaustive accessibility audit, or broad browser test
suite unless the active issue requires it. Do not encode current routes,
selectors, commands, page layouts, or framework recipes as permanent guidance.
Do not add governance documents or change engineering/GitHub ownership.

Report the result and mode, surfaces changed or reviewed, journey assessed,
rendered/browser behavior actually verified, validation evidence, blocking and
nonblocking findings, missing evidence, and unresolved risks. Recommend bounded
follow-ups only when supported. Use engineering's proportional validation and
reporting rules; this skill adds no automatic deployment, commit, or approval.
