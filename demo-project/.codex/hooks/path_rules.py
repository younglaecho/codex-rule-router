#!/usr/bin/env python3
"""Run the plugin implementation from this standalone demo project."""

from pathlib import Path
import runpy


PLUGIN_HOOK = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "codex-path-rules"
    / "hooks"
    / "path_rules.py"
)
runpy.run_path(str(PLUGIN_HOOK), run_name="__main__")
