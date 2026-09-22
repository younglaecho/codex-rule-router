# Codex Path Rules

Path-scoped repository instructions for Codex, similar to path-filtered rule
files in other coding agents.

The plugin watches local tool calls with a `PreToolUse` hook. When a tool touches
a path matching `.codex/rules/**/*.md`, it stops that first call and returns the
matching instructions to Codex. Codex then retries with those rules in context.

## Install

```bash
codex plugin marketplace add younglaecho/codex-path-rules --ref main
codex plugin add codex-path-rules@codex-path-rules
```

Start a new Codex session after installation. Review and trust the bundled hook
when Codex prompts you.

For local development, replace `younglaecho/codex-path-rules --ref main` with
the path to this repository.

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

The hook runs for local tool calls, including `apply_patch`, shell tools, and MCP
tools. It extracts paths from file tool arguments, patch headers, and path-like
shell tokens. Each matching rule is loaded once per Codex session. A changed
rule is loaded again immediately, and compaction resets the loaded-rule state.

The first matching call is denied on purpose. This prevents an edit from running
before Codex receives its scoped instructions. The rule text is sent through
`hookSpecificOutput.additionalContext`; the denial reason asks Codex to retry
without user intervention.

The frontmatter is compatible with Claude-style `paths`. To share the same rule
files, point `.codex/rules` at an existing `.claude/rules` directory with a
symlink, or generate both directories from a shared source.

`AGENTS.md` remains the right place for instructions that always apply. Use this
plugin for conventions that depend on the files being accessed.

## Check rules locally

From this repository:

```bash
python3 plugins/codex-path-rules/hooks/path_rules.py --validate --cwd /path/to/project
python3 plugins/codex-path-rules/hooks/path_rules.py --check fe/app/page.tsx --event edit --cwd /path/to/project
```

For an end-to-end Codex test without installing the plugin, follow
[`VERIFY.md`](./VERIFY.md). It uses `demo-project/.codex/hooks.json` and the same
hook implementation shipped by the plugin.

## Limits

- Arbitrary programs can construct file paths at runtime, so shell path detection
  is best effort. `apply_patch` paths are detected precisely.
- Hook instructions cannot override system, developer, or user instructions.
- A rule file is limited to 16 KiB and combined matching context to 48 KiB.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 /path/to/plugin-creator/scripts/validate_plugin.py plugins/codex-path-rules
```

## License

MIT
