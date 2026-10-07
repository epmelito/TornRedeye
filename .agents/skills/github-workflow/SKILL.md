---
name: github-workflow
description: Create, update, and finalize Torn Redeye GitHub issues, branches, commits, pushes, and pull requests. Use when repository work requires labels, assignees, issue comments, branch setup, commits, pull request creation, issue linking, metadata verification, or other routine GitHub workflow actions.
---

# GitHub workflow

## Responsibility

Complete authorized Torn Redeye GitHub workflow actions accurately and with
minimal user interaction.

Discover the repository and GitHub state, perform the complete bounded workflow,
verify the resulting state, fix routine omissions, and return one concise
report.

## Autonomous workflow

When the requested outcome is clear:

1. Read the active issue and applicable repository guidance.
2. Inspect the current branch, working tree, remote, available labels, and
   relevant GitHub state.
3. Perform the authorized issue, branch, commit, push, or pull request actions.
4. Apply metadata appropriate to the specific work.
5. Verify the resulting GitHub and repository state.
6. Fix routine omissions caused by the workflow.
7. Return one completion report.

Do not ask for confirmation before routine inspection, issue comments, applying
an existing appropriate label, assigning the repository owner, pushing the
authorized branch, or creating a requested pull request.

When approved work is ready, stage authorized files, commit, push, create or
update the pull request, apply metadata, and verify the result in one
invocation. Do not require a separate routine commit-preparation task.

Use recorded implementation and review evidence. Do not rerun application tests
or lint during GitHub finalization unless the evidence is missing, stale,
contradictory, or invalidated by later changes.

## Startup reconciliation

Before starting a new work package or another mutation-based GitHub workflow,
reconcile the working tree, current local branch, remote branch, pull request,
and linked issue state without requiring a separate user request.

When the current branch belongs to a merged pull request, verify the merge and
expected issue closure, switch to and synchronize the target branch, confirm
the merged change is present, and delete the merged local feature branch when
safe.

When the corresponding remote feature branch still exists and is no longer
needed, verify it is not protected, is not used by an open pull request, and is
not retained by repository policy. Delete it, prune stale remote-tracking
references, and verify it no longer exists.

Verify the working tree is clean before starting new work.

Stop when reconciliation would discard local changes, delete an unmerged
branch, conflict with repository policy, or require a genuine decision.
Read-only GitHub tasks do not mutate the local repository unless their result
depends on local state.

## Dynamic labels

- Inspect the available repository labels and the active work package.
- Apply the smallest set of existing labels that accurately describes the work.
- Do not reuse labels merely because they were used on a previous issue or pull
  request.
- Do not use fixed labels for every workflow.
- Create a new label only when no existing label fits and the category is
  expected to recur.
- Give a new label a short factual description.
- Ask before creating a label when its category is questionable, overly narrow,
  or likely to be used only once.
- Apply matching labels to the pull request when they remain accurate for the
  delivered work.

## Issues and pull requests

- Keep issue bodies proportional to the work. Define the intended outcome,
  material product and architecture boundaries, delivery constraints, acceptance
  criteria, and required verification without turning the issue into a full
  implementation specification.
- Omit exact schemas, file layouts, query details, test inventories, UI
  composition, and other implementation choices unless they are required to
  preserve an accepted contract, prevent a known risk, or resolve an approved
  decision.
- Before creating or updating an issue, remove repeated requirements,
  repository discoverable detail, speculative decisions, and tests already
  implied by acceptance criteria.

- Assign newly created issues and pull requests to the repository owner unless
  instructed otherwise.
- Link completed work using `Closes #<issue>` when the pull request should close
  the active issue.
- Before merge, recommend `Squash and merge` for normal feature, fix,
  documentation, and maintenance pull requests unless a concrete reason
  justifies another method.
- Preserve existing issue or pull request content unless a change is authorized.
- After creating or updating a pull request, verify:
  - target branch
  - source branch
  - changed files
  - included commits
  - labels
  - assignee
  - issue closing linkage
  - pull request URL
- Do not imply that metadata was applied unless the resulting GitHub state
  confirms it.

## Branches and commits

- Follow repository branch and commit conventions.
- Avoid hyphenating words in commit messages, pull request descriptions, and
  GitHub comments.
- Stage only files authorized by the active work package.
- Do not include unrelated working tree changes.
- Confirm the commit contains the intended paths before pushing.
- Do not force push.
- Delete a local or remote feature branch only through safe startup
  reconciliation or when explicitly requested.

## Human decision gates

Stop and request input only when:

- the requested outcome is materially ambiguous
- the target or base branch cannot be determined safely
- unrelated working tree changes create a conflict
- a new label would represent a questionable or one time category
- the action would merge, close, reopen, overwrite, delete anything other than
  a verified merged feature branch during startup reconciliation, or otherwise
  make a destructive or difficult-to-reverse change
- required permissions are unavailable
- an external action falls outside the authorized work package

State the exact decision or permission needed.

## Boundaries

- Do not merge pull requests unless explicitly requested.
- Do not modify repository files unless the task includes repository changes.
- Do not change unrelated issue or pull request content.
- Do not repeat actions that have already been completed and verified.
- Do not report success based only on command completion.
- Do not hide incomplete or failed GitHub actions.

## Completion report

Report only:

- result
- issue or pull request links
- branch and commit details
- changed paths
- labels and assignee
- issue closing linkage
- validation and resulting state verified
- unresolved permissions, risks, or blocked actions

Keep the report concise.
