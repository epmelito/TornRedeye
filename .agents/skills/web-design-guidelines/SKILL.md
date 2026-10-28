---
name: web-design-guidelines
description: Review UI code for Web Interface Guidelines compliance. Use when asked to "review my UI", "check accessibility", "audit design", "review UX", or "check my site against best practices".
metadata:
  author: vercel
  version: "1.0.0"
  argument-hint: <file-or-pattern>
---

# Web Interface Guidelines

TornRedeye modification (2026-10-28): the upstream review skill's remote rules
are bundled and pinned locally for reproducible, instruction-only use. See
[upstream provenance](../EXTERNAL-DESIGN.md) for versions and licensing.

Review files for compliance with Web Interface Guidelines.

## How It Works

1. Read the pinned guidelines in [references/command.md](references/command.md)
2. Read the specified files (or prompt user for files/pattern)
3. Check against all rules in the bundled guidelines
4. Output findings in the terse `file:line` format

## Guidelines Source

The local rules are pinned to this source revision:

```
https://raw.githubusercontent.com/vercel-labs/web-interface-guidelines/434b7f91364665f2f733b310ec54809bf8f37937/command.md
```

Read `references/command.md` locally. It contains the rules and output format
instructions; no network tool or runtime dependency is required. Refresh the
vendored rules only as an explicit, reviewed update.

## Usage

When a user provides a file or pattern argument:
1. Read the bundled guidelines referenced above
2. Read the specified files
3. Apply all rules from the bundled guidelines
4. Output findings using the format specified in the guidelines

If no files specified, ask the user which files to review.
