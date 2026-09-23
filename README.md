# Codex Rule Router

Path-scoped repository instructions for Codex and GitHub Copilot CLI, similar
to path-filtered rule files in other coding agents.

The plugin watches tool calls with a `PreToolUse` hook. When a tool touches a
path matching `.codex/rules/**/*.md`, it stops that first call and returns the
matching instructions to the agent. The agent then retries with those rules.

## Install for Codex

```bash
codex plugin marketplace add younglaecho/codex-rule-router --ref main
codex plugin add codex-rule-router@codex-rule-router
```

Start a new Codex session after installation. Review and trust the bundled hook
when Codex prompts you.

For local development, replace `younglaecho/codex-rule-router --ref main` with
the path to this repository.

## Install for GitHub Copilot CLI

```bash
copilot plugin marketplace add younglaecho/codex-rule-router
copilot plugin install codex-rule-router@codex-rule-router
```

Start a new Copilot CLI session after installation. The Copilot adapter uses the
same `.codex/rules/**/*.md` files as Codex.

Copilot hook support currently covers Copilot CLI. GitHub's hook API is also
available to Copilot cloud agent through repository configuration, but this
plugin has only been verified end to end with Copilot CLI. GitHub Copilot IDE
extensions do not currently load these CLI hooks.

## Configure a repository

Create `.codex/rules/frontend.md` in the Git repository root:

```markdown
---
paths:
  - "fe/**/*.{ts,tsx,css,scss}"
exclude:
  - "fe/**/*.test.{ts,tsx}"
events:
  - read
  - edit
priority: 100
---

# Frontend rules

- Read `docs/common/fe-coding-convention/README.md` before implementation.
- Reuse the design system before adding a component.
- Run the frontend type check after changing TypeScript.
```

Patterns are relative to the repository root. Supported syntax:

- `*` matches within one path segment.
- `**` matches across directories.
- `?` matches one non-separator character.
- `{ts,tsx}` expands alternatives.
- `exclude` and a leading `!` exclude matching paths from that rule.
- `events` can limit a rule to `read`, `edit`, or both.
- Higher `priority` rules are injected first. The default is `0`.

Use multiple rule files to keep instructions short and focused:

```text
.codex/rules/
├── frontend.md
├── backend.md
└── database.md
```

## Behavior

The hook runs for local tool calls, including patch, file, shell, and MCP tools.
It extracts paths from file tool arguments, patch headers, and path-like shell
tokens. Each matching rule is loaded once per agent session. A changed rule is
loaded again immediately, and compaction resets the loaded-rule state.

The first matching call is denied on purpose. This prevents an edit from running
before the agent receives its scoped instructions. Codex receives the rule text
through `hookSpecificOutput.additionalContext`. Copilot receives the same text
in `permissionDecisionReason`, which is returned to the agent after the denial.
Both agents then retry without user intervention.

The frontmatter is compatible with Claude-style `paths`. To share the same rule
files, point `.codex/rules` at an existing `.claude/rules` directory with a
symlink, or generate both directories from a shared source.

`AGENTS.md` remains the right place for instructions that always apply. Use this
plugin for conventions that depend on the files being accessed.

## Check rules locally

From this repository:

```bash
python3 plugins/codex-rule-router/hooks/path_rules.py --validate --cwd /path/to/project
python3 plugins/codex-rule-router/hooks/path_rules.py --check fe/app/page.tsx --event edit --cwd /path/to/project
```

For end-to-end Codex and Copilot CLI checks, follow [`VERIFY.md`](./VERIFY.md).

## Limits

- Arbitrary programs can construct file paths at runtime, so shell path detection
  is best effort. `apply_patch` paths are detected precisely.
- Hook instructions cannot override system, developer, or user instructions.
- A rule file is limited to 16 KiB and combined matching context to 48 KiB.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/plugin-creator/scripts/validate_plugin.py plugins/codex-rule-router
```

## License

MIT
