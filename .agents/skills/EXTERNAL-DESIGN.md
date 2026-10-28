# External design guidance

Project-scoped, instruction-only copies reviewed on 2026-10-28 for
[Issue #75's approved design direction](https://github.com/epmelito/TornRedeye/issues/75#issuecomment-6456555496).
No design concept is selected by this installation. The active issue, approved
brief, `AGENTS.md`, engineering and reporting-engineering remain authoritative.

## Installed sources and exact files

Paths below are relative to `.agents/skills/`. Full commit IDs pin the content;
version labels alone are not update selectors.

| Source | Pinned commit | License | Local files |
| --- | --- | --- | --- |
| [anthropics/skills](https://github.com/anthropics/skills/tree/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design) | `dbd4588f9e1033efb41dad4bef2f7947c8993d44` | Apache-2.0 | `frontend-design/SKILL.md`, `frontend-design/LICENSE.txt` |
| [vercel-labs/agent-skills](https://github.com/vercel-labs/agent-skills/tree/1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62/skills/web-design-guidelines), skill metadata version 1.0.0 | `1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62` | MIT, declared in upstream README | `web-design-guidelines/SKILL.md` (adapted as described below) |
| [vercel-labs/web-interface-guidelines](https://github.com/vercel-labs/web-interface-guidelines/tree/434b7f91364665f2f733b310ec54809bf8f37937) | `434b7f91364665f2f733b310ec54809bf8f37937` | MIT, copyright 2025 Vercel Labs | `web-design-guidelines/references/command.md`, `web-design-guidelines/references/LICENSE` |
| [pbakaus/impeccable](https://github.com/pbakaus/impeccable/tree/d34c87e1a00989ae8dbdd7f6b30b70f23319e860), package/skill version 4.8.0 | `d34c87e1a00989ae8dbdd7f6b30b70f23319e860` | Apache-2.0, copyright 2025 Paul Bakaus | `reporting-engineering/references/impeccable/operate.md`, `craft-floor.md`, `LICENSE` (all in that directory) |

Anthropic's two files are copied from `skills/frontend-design/` unchanged.
Its skill directory contains no scripts or dependencies. The repository-wide
third-party notices cover software/fonts not included in this instruction copy.

The Vercel skill is copied from `skills/web-design-guidelines/SKILL.md` with a
prominent modification notice: latest-remote-fetch instructions now read the
bundled, pinned `references/command.md`. Name, description and review purpose
are retained; this is an adaptation of the upstream skill, not a replacement.
The rules and their license are unchanged copies of `command.md` and `LICENSE`
from the separately pinned guidelines repository. The agent-skills repository
declares MIT in its [README](https://github.com/vercel-labs/agent-skills/blob/1a2c79d4e6c4a2283dc2c614b6538cbe1ed5ca62/README.md#license)
but ships no standalone license file at this revision; the included license is
specifically from the guidelines repository, not an invented agent-skills notice.
No npm packages are needed to read either skill; the agent-skills repository's
`yaml` dependency serves its tooling and is not installed here.

Impeccable's two reference texts are unchanged copies from `skill/reference/`.
They add product-interface guidance and a craft checklist without copying its
main `SKILL.md`, command workflows or executable integration. The license is
copied unchanged from repository root. Upstream `NOTICE.md` concerns derived
iOS/Android references, which are not included. The references' mentions of
hooks, modes and upstream `SKILL.md` describe the full distribution; they do not
enable it or authorize execution here. `operate.md` links to the included craft
floor. Use the existing reporting skill for local scope and precedence.

## Supported upstream installation paths reviewed

- Anthropic documents Claude plugin marketplace installation and skill uploads;
  its folders use the Agent Skills format. A direct copy of the selected folder
  supplies all instruction content needed by Codex. No Claude plugin is installed.
- Vercel documents `npx skills add vercel-labs/agent-skills` (and selection with
  `--skill web-design-guidelines`). The selected skill is Markdown only; manual
  copying plus the pinned rules avoids executing its installer or fetching mutable
  rules at review time. No other Vercel skills are installed.
- Impeccable documents an npm CLI installer, Git/submodule linking, provider
  plugins, ZIP downloads and manual provider-folder copying. Its current main
  skill calls `scripts/impeccable context`; the launcher runs a platform engine
  shipped alongside it or downloaded into `~/.impeccable/bin/` on first use.
  Its documented refusal/failure fallback reads project context directly, but
  later workflows still reference engine commands, so this setup does not expose
  the full skill with dangling executable dependencies.

Full Impeccable installation would require a separate decision to accept and
review its platform binary/launcher and command workflows. The npm installer
requires Node >=18.19 and uses optional platform binary packages (0.1.18 at this
revision); manual provider copies do not need Node but still invoke the engine.
Its Codex installation also installs a project `.codex/hooks.json` and asks for
hook trust. No installer, binary, launcher, agent configuration, hook, trust or
security-setting change is included or approved here. Live browser iteration and
automatic detector checks are unavailable. Useful instruction references are
available now; full executable integration is deferred, not required for exploration.

## Discovery and maintenance

Codex discovers the `frontend-design` and `web-design-guidelines` manifests under
the repository's `.agents/skills/`; explicitly invoke them by those names when
needed. Impeccable references are reached through `reporting-engineering`, not
through a separate `impeccable` skill. See the
[official skill discovery documentation](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
If a new skill does not appear in a session's selector, restart Codex; direct
file readability alone does not prove that a running selector has refreshed.

To update, review the selected upstream files, licenses, dependencies and relevant
notices at a new full commit ID; compare the changes, refresh only the selected
texts, preserve the Vercel local-rules adaptation and update this provenance.
Do not run upstream installers or silently switch back to mutable remote rules.
External framework examples, typography/case preferences, absolute aesthetic
rules and performance thresholds do not supersede TornRedeye's approved brief
or justify dependencies, refactoring or an expanded audit by themselves.
