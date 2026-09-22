# Verify in Codex

This demo exercises the same hook implementation through a repository-local
`.codex/hooks.json`, so it does not require installing the plugin.

1. Open `/Users/joyeonglae/develop/codex-rule-router/demo-project` as a new local
   Codex project.
2. Trust the project and review/allow its local hooks when Codex prompts.
3. Start a fresh task in that project.
4. Send this prompt exactly:

   ```text
   fe/result.txt의 내용을 after로 바꿔줘.
   ```

5. Inspect `fe/result.txt`.

Expected content:

```text
after
PATH_RULES_LOADED=true
```

Expected lifecycle:

1. The first edit matches `.codex/rules/frontend.md`.
2. `PreToolUse` returns `deny` and injects the rule with `additionalContext`.
3. Codex retries the edit with the rule applied.
4. Later edits in the same session run without another denial unless the rule
   changes or the conversation is compacted.

Reset `fe/result.txt` to `before` before repeating the test in a fresh task.
