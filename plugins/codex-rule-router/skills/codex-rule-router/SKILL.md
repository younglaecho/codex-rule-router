---
name: codex-rule-router
description: Configure, validate, or troubleshoot repository rules that apply automatically based on file paths. Use when a user asks for Claude-style path rules, scoped coding conventions, or .codex/rules setup.
---

# Codex Rule Router

Store scoped instructions in `.codex/rules/**/*.md` at the Git repository root.

Use this format:

```markdown
---
paths:
  - "fe/**/*.{ts,tsx}"
exclude:
  - "fe/**/*.test.tsx"
events:
  - read
  - edit
priority: 100
---

# Frontend rules

- Use the shared design-system components.
- Run the frontend type check after changing TypeScript.
```

Patterns are repository-relative. `*`, `**`, `?`, brace alternatives such as
`*.{ts,tsx}`, a separate `exclude` list, and leading `!` exclusions are
supported. `events` accepts `read` and `edit`; omitting it enables both. Rules
with higher `priority` are injected first.

Keep each rule focused. Put instructions that always apply in `AGENTS.md`; use
path rules only when the instruction depends on the file being accessed.

The hook stops the first matching tool call in each Codex session, injects the
matching instructions through `additionalContext`, and asks Codex to retry.
Compaction clears the loaded-rule state so required instructions are injected
again.

To validate or inspect matching behavior, run the hook script from the installed
plugin directory with `--validate` or `--check <path>`. Never edit plugin state
files under `PLUGIN_DATA` manually.
