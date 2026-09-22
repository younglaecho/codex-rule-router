from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "plugins" / "codex-rule-router" / "hooks" / "path_rules.py"
SPEC = importlib.util.spec_from_file_location("path_rules", SCRIPT)
assert SPEC and SPEC.loader
path_rules = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = path_rules
SPEC.loader.exec_module(path_rules)


class PathRulesTest(unittest.TestCase):
    def make_project(self, rule_text: str):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / ".codex" / "rules").mkdir(parents=True)
        (root / ".codex" / "rules" / "frontend.md").write_text(rule_text, encoding="utf-8")
        return temporary, root

    def test_globs_support_recursive_braces_and_exclusions(self):
        temporary, root = self.make_project(
            """---
paths:
  - "fe/**/*.{ts,tsx}"
  - "!fe/**/*.test.tsx"
---
Use React conventions.
"""
        )
        self.addCleanup(temporary.cleanup)
        rules, errors = path_rules.load_rules(root)
        self.assertEqual(errors, [])
        self.assertTrue(rules[0].matches("fe/page.tsx", "edit"))
        self.assertTrue(rules[0].matches("fe/app/home/page.ts", "read"))
        self.assertFalse(rules[0].matches("fe/app/home/page.test.tsx", "edit"))
        self.assertFalse(rules[0].matches("be/page.ts", "edit"))

    def test_globs_preserve_leading_dot_directories(self):
        self.assertTrue(path_rules.glob_matches(".github/**", ".github/workflows/ci.yml"))
        self.assertFalse(path_rules.glob_matches(".github/**", "github/workflows/ci.yml"))

    def test_apply_patch_extracts_every_file(self):
        command = """*** Begin Patch
*** Update File: fe/app/page.tsx
*** Add File: fe/app/new.ts
*** Delete File: old.txt
*** End Patch"""
        candidates = path_rules.extract_candidates("apply_patch", {"command": command})
        self.assertEqual(candidates, {"fe/app/page.tsx", "fe/app/new.ts", "old.txt"})

    def test_first_match_in_turn_denies_then_allows(self):
        temporary, root = self.make_project(
            """---
paths: ["fe/**/*.tsx"]
---
Use the shared component library.
"""
        )
        self.addCleanup(temporary.cleanup)
        data = root / "plugin-data"
        event = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(root),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Begin Patch\n*** Update File: fe/app/page.tsx\n*** End Patch"},
        }
        first = path_rules.process_event(event, {"PLUGIN_DATA": str(data)})
        second = path_rules.process_event(event, {"PLUGIN_DATA": str(data)})
        self.assertEqual(first["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertEqual(
            first["hookSpecificOutput"]["permissionDecisionReason"],
            "Path-specific rules were loaded. Retry the tool call after applying them.",
        )
        self.assertIn("Use the shared component library.", first["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(second)

    def test_rule_is_loaded_once_per_session(self):
        temporary, root = self.make_project(
            """---
paths:
  - fe/**
---
Read frontend instructions.
"""
        )
        self.addCleanup(temporary.cleanup)
        data = root / "plugin-data"
        base = {
            "session_id": "session-1",
            "cwd": str(root),
            "tool_name": "Bash",
            "tool_input": {"command": "sed -n '1,20p' fe/package.json"},
        }
        self.assertIsNotNone(path_rules.process_event({**base, "turn_id": "turn-1"}, {"PLUGIN_DATA": str(data)}))
        self.assertIsNone(path_rules.process_event({**base, "turn_id": "turn-2"}, {"PLUGIN_DATA": str(data)}))

    def test_compaction_resets_session_rules(self):
        temporary, root = self.make_project(
            """---
paths: ["fe/**"]
---
Frontend.
"""
        )
        self.addCleanup(temporary.cleanup)
        data = root / "plugin-data"
        edit = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(root),
            "hook_event_name": "PreToolUse",
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Update File: fe/a.ts"},
        }
        self.assertIsNotNone(path_rules.process_event(edit, {"PLUGIN_DATA": str(data)}))
        self.assertIsNone(path_rules.process_event(edit, {"PLUGIN_DATA": str(data)}))
        path_rules.process_event(
            {
                "session_id": "session-1",
                "cwd": str(root),
                "hook_event_name": "SessionStart",
                "source": "compact",
            },
            {"PLUGIN_DATA": str(data)},
        )
        self.assertIsNotNone(path_rules.process_event(edit, {"PLUGIN_DATA": str(data)}))

    def test_explicit_exclude_events_and_priority(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        rules_dir = root / ".codex" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "low.md").write_text(
            """---
paths: ["frontend/**"]
events: [edit]
priority: 10
---
Low priority.
""",
            encoding="utf-8",
        )
        (rules_dir / "high.md").write_text(
            """---
paths:
  - frontend/**
exclude:
  - frontend/generated/**
events:
  - read
  - edit
priority: 100
---
High priority.
""",
            encoding="utf-8",
        )
        rules, errors = path_rules.load_rules(root)
        self.assertEqual(errors, [])
        edit_matches = path_rules.matching_rules(rules, ["frontend/page.tsx"], "edit")
        self.assertEqual([rule.priority for rule in edit_matches], [100, 10])
        read_matches = path_rules.matching_rules(rules, ["frontend/page.tsx"], "read")
        self.assertEqual([rule.priority for rule in read_matches], [100])
        generated = path_rules.matching_rules(rules, ["frontend/generated/page.tsx"], "read")
        self.assertEqual(generated, [])

    def test_mcp_read_path_loads_read_rule(self):
        temporary, root = self.make_project(
            """---
paths: ["docs/**"]
events: ["read"]
---
Read rule.
"""
        )
        self.addCleanup(temporary.cleanup)
        event = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(root),
            "hook_event_name": "PreToolUse",
            "tool_name": "mcp__filesystem__read_file",
            "tool_input": {"path": "docs/guide.md"},
        }
        output = path_rules.process_event(event, {"PLUGIN_DATA": str(root / "data")})
        self.assertIn("Read rule.", output["hookSpecificOutput"]["additionalContext"])

    def test_changed_rule_is_reloaded_in_same_turn(self):
        temporary, root = self.make_project(
            """---
paths: ["fe/**"]
---
Version one.
"""
        )
        self.addCleanup(temporary.cleanup)
        data = root / "plugin-data"
        event = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(root),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Update File: fe/a.ts"},
        }
        self.assertIsNotNone(path_rules.process_event(event, {"PLUGIN_DATA": str(data)}))
        rule = root / ".codex" / "rules" / "frontend.md"
        rule.write_text(rule.read_text().replace("Version one.", "Version two."), encoding="utf-8")
        output = path_rules.process_event(event, {"PLUGIN_DATA": str(data)})
        self.assertIn("Version two.", json.dumps(output))

    def test_invalid_rule_fails_closed(self):
        temporary, root = self.make_project("No frontmatter")
        self.addCleanup(temporary.cleanup)
        event = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "cwd": str(root),
            "tool_name": "apply_patch",
            "tool_input": {"command": "*** Update File: anything.txt"},
        }
        output = path_rules.process_event(event, {"PLUGIN_DATA": str(root / "data")})
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("Invalid Codex path rule", json.dumps(output))

    def test_nested_cwd_normalizes_project_relative_path(self):
        temporary, root = self.make_project(
            """---
paths: ["fe/**/*.ts"]
---
Frontend.
"""
        )
        self.addCleanup(temporary.cleanup)
        cwd = root / "fe"
        cwd.mkdir()
        self.assertEqual(path_rules.normalize_candidate("src/a.ts", cwd, root), "fe/src/a.ts")
        self.assertEqual(path_rules.normalize_candidate("fe/src/a.ts", cwd, root), "fe/src/a.ts")
        self.assertEqual(path_rules.normalize_candidate("../.github/workflows/ci.yml", cwd, root), ".github/workflows/ci.yml")

    def test_cwd_with_rules_is_root_even_inside_parent_git_repository(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(parent)], check=True)
        child = parent / "demo"
        (child / ".codex" / "rules").mkdir(parents=True)
        self.assertEqual(path_rules.find_project_root(child), child.resolve())


if __name__ == "__main__":
    unittest.main()
