#!/usr/bin/env python3
"""Codex PreToolUse hook for repository path-scoped rules.

Rule files live under <repository>/.codex/rules/**/*.md. Each rule has a
small YAML-compatible frontmatter subset with a required `paths` list.
The first matching tool call in a session is denied while the rule text is
added to model context. Codex can then retry with those rules available.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


MAX_RULE_BYTES = 16_384
MAX_CONTEXT_BYTES = 48_000
PATH_KEYS = {"file", "files", "file_path", "file_paths", "path", "paths"}
PATCH_PATH_RE = re.compile(
    r"^\*\*\* (?:Add|Update|Delete) File:\s*(.+?)\s*$|"
    r"^\*\*\* Move to:\s*(.+?)\s*$",
    re.MULTILINE,
)
COMMAND_PATH_RE = re.compile(
    r"(?<![\w@+.-])"
    r"((?:\.{0,2}/|/)?(?:[\w@+.-]+/)+[\w@+.-]+|"
    r"(?:\.{0,2}/)?[\w@+-]+\.[A-Za-z0-9_-]+)"
)
EDIT_TOOL_RE = re.compile(r"(?:apply[_-]?patch|edit|write|create|delete|remove|move|rename|mkdir)", re.I)
BASH_EDIT_RE = re.compile(
    r"(?:^|[;&|]\s*|\s)(?:rm|mv|cp|touch|mkdir|install|truncate)\s|"
    r"(?:^|[;&|]\s*|\s)(?:sed|perl)\s+[^\n;&|]*?(?:\s-i|--in-place)|"
    r"(?:^|[;&|]\s*|\s)tee(?:\s|$)|"
    r"(?:^|[^<>])>>?(?!=)",
    re.I,
)
VALID_EVENTS = {"read", "edit"}


class RuleError(ValueError):
    """Raised when a rule file is malformed or unsafe."""


@dataclass(frozen=True)
class Rule:
    source: Path
    display_source: str
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    events: tuple[str, ...]
    priority: int
    body: str
    digest: str

    def matches(self, relative_path: str, event_kind: str) -> bool:
        included = any(glob_matches(pattern, relative_path) for pattern in self.include)
        excluded = any(glob_matches(pattern, relative_path) for pattern in self.exclude)
        return event_kind in self.events and included and not excluded


def _strip_scalar(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if value[0:1] in {"'", '"'}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value.strip("'\"")
        return str(parsed)
    return value.split(" #", 1)[0].strip()


def _parse_inline_list(value: str) -> list[str]:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        if not value.endswith("]"):
            raise RuleError("inline lists must end with `]`")
        inner = value[1:-1]
        try:
            parsed = next(csv.reader([inner], skipinitialspace=True))
        except (csv.Error, StopIteration) as exc:
            raise RuleError("invalid inline list") from exc
    if not isinstance(parsed, (list, tuple)) or not all(isinstance(v, str) for v in parsed):
        raise RuleError("inline lists must contain only strings")
    return [_strip_scalar(str(v)) for v in parsed if _strip_scalar(str(v))]


def _parse_frontmatter(lines: Sequence[str]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    active_list: str | None = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if active_list and stripped.startswith("-"):
            metadata.setdefault(active_list, []).append(_strip_scalar(stripped[1:]))
            continue
        match = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", stripped)
        if not match:
            raise RuleError(f"unsupported frontmatter line: {stripped!r}")
        key, value = match.group(1), match.group(2).strip()
        active_list = key if key in {"paths", "exclude", "events"} else None
        if active_list:
            metadata[active_list] = []
            if value:
                if value.startswith("["):
                    metadata[active_list].extend(_parse_inline_list(value))
                else:
                    metadata[active_list].append(_strip_scalar(value))
        else:
            metadata[key] = _strip_scalar(value)
    return metadata


def _normalize_pattern(original: str) -> tuple[str, bool]:
    original = original.replace("\\", "/").strip()
    is_exclusion = original.startswith("!")
    pattern = original[1:] if is_exclusion else original
    while pattern.startswith("./") or pattern.startswith("/"):
        pattern = pattern[2:] if pattern.startswith("./") else pattern[1:]
    if not pattern or ".." in PurePosixPath(pattern).parts:
        raise RuleError(f"unsafe path pattern: {original!r}")
    return pattern, is_exclusion


def parse_rule(path: Path, rules_root: Path) -> Rule:
    raw = path.read_bytes()
    if len(raw) > MAX_RULE_BYTES:
        raise RuleError(f"rule exceeds {MAX_RULE_BYTES} bytes")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuleError("rule must be UTF-8") from exc

    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RuleError("rule must start with frontmatter (`---`)")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration as exc:
        raise RuleError("frontmatter is missing its closing `---`") from exc

    metadata = _parse_frontmatter(lines[1:end])
    patterns = [str(p).strip() for p in metadata.get("paths", []) if str(p).strip()]
    if not patterns:
        raise RuleError("frontmatter requires at least one path pattern")

    include: list[str] = []
    exclude: list[str] = [str(p).strip() for p in metadata.get("exclude", []) if str(p).strip()]
    for original in patterns:
        pattern, is_exclusion = _normalize_pattern(original)
        (exclude if is_exclusion else include).append(pattern)
    exclude = [_normalize_pattern(pattern)[0] for pattern in exclude]
    if not include:
        raise RuleError("a rule requires at least one non-negated path pattern")

    raw_events = metadata.get("events", ["read", "edit"])
    events = tuple(str(event).lower().strip() for event in raw_events if str(event).strip())
    if not events or any(event not in VALID_EVENTS for event in events):
        raise RuleError("events may contain only `read` and `edit`")
    try:
        priority = int(metadata.get("priority", 0))
    except (TypeError, ValueError) as exc:
        raise RuleError("priority must be an integer") from exc

    resolved = path.resolve()
    try:
        resolved.relative_to(rules_root.resolve())
    except ValueError as exc:
        raise RuleError("rule symlink resolves outside .codex/rules") from exc

    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        raise RuleError("rule body cannot be empty")
    display_source = ".codex/rules/" + resolved.relative_to(rules_root.resolve()).as_posix()
    return Rule(
        source=resolved,
        display_source=display_source,
        include=tuple(include),
        exclude=tuple(exclude),
        events=events,
        priority=priority,
        body=body,
        digest=hashlib.sha256(raw).hexdigest(),
    )


def load_rules(project_root: Path) -> tuple[list[Rule], list[str]]:
    rules_root = project_root / ".codex" / "rules"
    if not rules_root.is_dir():
        return [], []
    rules: list[Rule] = []
    errors: list[str] = []
    for path in sorted(rules_root.rglob("*.md")):
        try:
            rule = parse_rule(path, rules_root)
            rules.append(rule)
        except (OSError, RuleError) as exc:
            try:
                display = path.relative_to(project_root).as_posix()
            except ValueError:
                display = str(path)
            errors.append(f"{display}: {exc}")
    return rules, errors


def expand_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]+)\}", pattern)
    if not match:
        return [pattern]
    choices = match.group(1).split(",")
    expanded: list[str] = []
    for choice in choices:
        expanded.extend(expand_braces(pattern[: match.start()] + choice + pattern[match.end() :]))
    return expanded


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    if pattern.endswith("/"):
        pattern += "**"
    prefix = r"(?:.*/)?" if "/" not in pattern else ""
    result = ["^", prefix]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    result.append(r"(?:.*/)?")
                    i += 1
                else:
                    result.append(r".*")
                continue
            result.append(r"[^/]*")
        elif char == "?":
            result.append(r"[^/]")
        else:
            result.append(re.escape(char))
        i += 1
    result.append("$")
    return re.compile("".join(result))


def glob_matches(pattern: str, relative_path: str) -> bool:
    path = relative_path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    path = path.lstrip("/")
    return any(glob_to_regex(expanded).match(path) for expanded in expand_braces(pattern))


def find_project_root(cwd: Path) -> Path:
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
        return Path(completed.stdout.strip()).resolve()
    except (FileNotFoundError, subprocess.SubprocessError):
        current = cwd.resolve()
        for candidate in (current, *current.parents):
            if (candidate / ".codex").is_dir():
                return candidate
        return current


def _walk_path_values(value: Any, key: str | None = None) -> Iterable[str]:
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            yield from _walk_path_values(child, str(child_key).lower())
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_path_values(child, key)
    elif isinstance(value, str) and key in PATH_KEYS:
        yield value


def extract_candidates(tool_name: str, tool_input: Any) -> set[str]:
    candidates = set(_walk_path_values(tool_input))
    command = tool_input.get("command", "") if isinstance(tool_input, Mapping) else ""
    if not isinstance(command, str):
        return candidates
    for match in PATCH_PATH_RE.finditer(command):
        candidates.add(next(group for group in match.groups() if group is not None))
    if tool_name == "Bash":
        for match in COMMAND_PATH_RE.finditer(command):
            value = match.group(1).rstrip("'\"),;:")
            if "://" not in value:
                candidates.add(value)
    return {value.strip() for value in candidates if value.strip()}


def normalize_candidate(candidate: str, cwd: Path, root: Path) -> str | None:
    candidate = candidate.strip().strip("'\"")
    if not candidate or candidate.startswith("-") or "\n" in candidate:
        return None
    raw = Path(candidate).expanduser()
    if raw.is_absolute():
        resolved = raw.resolve(strict=False)
    else:
        base = cwd
        try:
            cwd_relative = cwd.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            cwd_relative = ""
        normalized = candidate.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if cwd_relative and (normalized == cwd_relative or normalized.startswith(cwd_relative + "/")):
            base = root
        resolved = (base / raw).resolve(strict=False)
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def classify_event(tool_name: str, tool_input: Any) -> str:
    if tool_name == "Bash":
        command = tool_input.get("command", "") if isinstance(tool_input, Mapping) else ""
        return "edit" if isinstance(command, str) and BASH_EDIT_RE.search(command) else "read"
    return "edit" if EDIT_TOOL_RE.search(tool_name) else "read"


def matching_rules(rules: Sequence[Rule], paths: Sequence[str], event_kind: str) -> list[Rule]:
    matched = [rule for rule in rules if any(rule.matches(path, event_kind) for path in paths)]
    return sorted(matched, key=lambda rule: (-rule.priority, rule.display_source))


def _state_path(data_dir: Path, session_id: str) -> Path:
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32] + ".json"
    return data_dir / "sessions" / name


def _load_state(path: Path) -> set[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        loaded = value.get("loaded", [])
        if isinstance(loaded, list):
            return {str(digest) for digest in loaded}
    except (OSError, json.JSONDecodeError):
        pass
    return set()


def _save_state(path: Path, loaded: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": 2, "loaded": sorted(set(loaded))}, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    temporary.replace(path)


def build_rule_context(rules: Sequence[Rule], paths: Sequence[str]) -> str:
    sections = [
        "Codex path-scoped rules matched this tool call.",
        "Review and apply these repository instructions, then retry the tool call.",
        "Matched paths: " + ", ".join(sorted(paths)),
    ]
    for rule in rules:
        sections.append(
            f"\n[Rule: {rule.display_source}; priority={rule.priority}]\n{rule.body}"
        )
    context = "\n".join(sections)
    if len(context.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise RuleError(f"matching rule context exceeds {MAX_CONTEXT_BYTES} bytes")
    return context


def deny(reason: str, additional_context: str | None = None) -> dict[str, Any]:
    specific: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }
    if additional_context:
        specific["additionalContext"] = additional_context
    return {
        "hookSpecificOutput": {
            **specific,
        }
    }


def process_event(event: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    env = os.environ if environ is None else environ
    cwd = Path(str(event.get("cwd") or os.getcwd())).resolve()
    root = find_project_root(cwd)
    session_id = str(event.get("session_id") or "unknown-session")
    data_dir = Path(env.get("PLUGIN_DATA") or (Path(tempfile.gettempdir()) / "codex-path-rules"))
    state_path = _state_path(data_dir, session_id)
    if event.get("hook_event_name") == "SessionStart":
        try:
            state_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None

    rules, errors = load_rules(root)
    if errors:
        return deny("Invalid Codex path rule configuration:\n" + "\n".join(f"- {error}" for error in errors))
    if not rules:
        return None

    tool_name = str(event.get("tool_name") or "")
    tool_input = event.get("tool_input", {})
    event_kind = classify_event(tool_name, tool_input)
    candidates = extract_candidates(tool_name, tool_input)
    paths = sorted(
        {
            normalized
            for candidate in candidates
            if (normalized := normalize_candidate(candidate, cwd, root)) is not None
        }
    )
    matched = matching_rules(rules, paths, event_kind)
    if not matched:
        return None

    loaded = _load_state(state_path)
    unseen = [rule for rule in matched if rule.digest not in loaded]
    if not unseen:
        return None

    try:
        context = build_rule_context(unseen, paths)
    except RuleError as exc:
        return deny(str(exc))
    _save_state(state_path, loaded | {rule.digest for rule in unseen})
    return deny(
        "Path-specific rules were loaded. Retry the tool call after applying them.",
        additional_context=context,
    )


def validate_command(cwd: Path) -> int:
    root = find_project_root(cwd)
    rules, errors = load_rules(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(f"Valid: {len(rules)} rule(s) under {root / '.codex' / 'rules'}")
    return 0


def check_command(cwd: Path, raw_paths: Sequence[str], event_kind: str) -> int:
    root = find_project_root(cwd)
    rules, errors = load_rules(root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    for raw_path in raw_paths:
        normalized = normalize_candidate(raw_path, cwd, root)
        matches = matching_rules(rules, [normalized], event_kind) if normalized else []
        print(f"{raw_path}: {', '.join(rule.display_source for rule in matches) or '(no match)'}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Codex path-scoped rules hook")
    parser.add_argument("--validate", action="store_true", help="validate rules in the current repository")
    parser.add_argument("--check", nargs="+", metavar="PATH", help="show rules matching one or more paths")
    parser.add_argument("--event", choices=sorted(VALID_EVENTS), default="edit", help="event used by --check")
    parser.add_argument("--cwd", type=Path, default=Path.cwd(), help="working directory for --check/--validate")
    args = parser.parse_args(argv)
    if args.validate:
        return validate_command(args.cwd.resolve())
    if args.check:
        return check_command(args.cwd.resolve(), args.check, args.event)

    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        print(f"Invalid hook input: {exc}", file=sys.stderr)
        return 1
    if not isinstance(event, Mapping):
        print("Invalid hook input: expected a JSON object", file=sys.stderr)
        return 1
    output = process_event(event)
    if output is not None:
        json.dump(output, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
