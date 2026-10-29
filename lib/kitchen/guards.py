"""`kitchen guards <repo>`: the kitchen's guards in one project, so they run there and nowhere else.

Copies hooks/ into the repo's .kitchen/hooks/ and runs each guard before every Bash call, and chef-mode-after-compact
after every compaction (SessionStart `compact`), from .claude/settings.json (Claude Code) and .codex/hooks.json (Codex).
Both files are merged: the kitchen's handlers are replaced, every other key and hook stays. A file that cannot be parsed is an error and nothing is written. `--check` writes nothing and fails
while anything differs from what this kitchen would write. Codex runs a new or changed project hook only after the
person trusts it in `/hooks`, and only in a trusted project.

Each handler runs the repo's own copy and fails closed (exit 2 blocks) when the copy is missing, but only while the
checkout still has the settings file: a session keeps the hooks it loaded, so after a switch to a branch without the
guards, a stale handler would otherwise block every Bash call.
Principles: `doors` (the guards), `no-fallbacks` (fail closed, refuse what cannot be parsed), `isolate` (one project,
reruns byte-identical), `encode-lessons` (rules that became guards).
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

VENDORED = ".kitchen/hooks"
TIMEOUT_SECONDS = 30


class GuardsError(Exception):
    pass


# Hooks that run after a compaction (SessionStart, source `compact`); every other executable in hooks/ is a Bash guard.
AFTER_COMPACT = ("chef-mode-after-compact",)


@dataclass
class Tool:
    name: str
    settings: str     # repo-relative file the tool reads project hooks from
    matcher: str      # PreToolUse: the shell tool
    compact: str      # SessionStart: the compaction source
    root: str         # shell code that sets $r to the project root, or exits 2


TOOLS = (
    Tool("Claude Code", ".claude/settings.json", "Bash", "compact",
         '[ -n "$CLAUDE_PROJECT_DIR" ] || { echo "kitchen: CLAUDE_PROJECT_DIR is not set" >&2; exit 2; }; r="$CLAUDE_PROJECT_DIR"'),
    # Codex sets no project variable and runs hooks in the session cwd; its docs say to resolve from the git root.
    Tool("Codex", ".codex/hooks.json", "^Bash$", "^compact$",
         'r=$(git rev-parse --show-toplevel 2>/dev/null) || { echo "kitchen: not inside a git repository" >&2; exit 2; }'),
)


def kitchen_files(kitchen: Path) -> dict[str, tuple[bytes, int]]:
    """name -> (bytes, mode) for every file the guards need: each executable guard and the parser they share."""
    folder = kitchen / "hooks"
    out = {}
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if path.is_file() and (path.suffix == ".py" or ("." not in path.name and os.access(path, os.X_OK))):
            out[path.name] = (path.read_bytes(), 0o644 if path.suffix == ".py" else 0o755)
    if not any(mode == 0o755 for _, mode in out.values()):
        raise GuardsError(f"this kitchen has no guards in {folder}")
    return out


def command(tool: Tool, name: str) -> str:
    """Runs the copy; any exit other than 0 (allow) or 2 (block), such as a copy that cannot execute, blocks too."""
    return (f"sh -c '{tool.root}; s=\"$r/{tool.settings}\"; h=\"$r/{VENDORED}/{name}\"; "
            f"[ -e \"$s\" ] || [ -L \"$s\" ] || exit 0; [ -x \"$h\" ] || {{ echo \"kitchen: guard missing: $h\" >&2; exit 2; }}; "
            f"\"$h\"; c=$?; [ $c -eq 0 ] || [ $c -eq 2 ] || {{ echo \"kitchen: guard $h failed with exit $c\" >&2; exit 2; }}; exit $c'")


# The handlers this command writes, and the form `kitchen init` wrote before it (Claude Code only).
MANAGED = (f'h="$r/{VENDORED}/', f'h="$CLAUDE_PROJECT_DIR/{VENDORED}/')


def is_kitchen_handler(handler) -> bool:
    return isinstance(handler, dict) and isinstance(handler.get("command"), str) \
        and handler["command"].startswith("sh -c '") and any(mark in handler["command"] for mark in MANAGED)


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GuardsError(f"{path} is not valid JSON ({error}); fix it, then rerun") from error
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    for event in ("PreToolUse", "SessionStart"):
        groups = hooks.get(event, []) if isinstance(hooks, dict) else None
        if not isinstance(groups, list) or not all(isinstance(g, dict) and isinstance(g.get("hooks", []), list) for g in groups):
            raise GuardsError(f"{path} does not have the shape hooks.{event}[] of {{matcher, hooks: [...]}}; fix it, then rerun")
    return data


def split(names: list[str]) -> tuple[list[str], list[str]]:
    """(Bash guards, after-compaction hooks) among the executables in hooks/."""
    return [n for n in names if n not in AFTER_COMPACT], [n for n in names if n in AFTER_COMPACT]


def merged(data: dict, tool: Tool, names: list[str]) -> dict:
    """data with the kitchen's handlers replaced by one group per event that runs each hook; nothing else changes."""
    result = copy.deepcopy(data)
    hooks = result.get("hooks", {})
    guards, after_compact = split(names)
    for event, matcher, mine in (("PreToolUse", tool.matcher, guards), ("SessionStart", tool.compact, after_compact)):
        groups = []
        for group in hooks.get(event, []):
            kept = [h for h in group.get("hooks", []) if not is_kitchen_handler(h)]
            if kept or not group.get("hooks"):
                groups.append({**group, "hooks": kept})
        if mine:
            groups.append({"matcher": matcher, "hooks": [
                {"type": "command", "command": command(tool, name), "timeout": TIMEOUT_SECONDS} for name in mine]})
        if groups or event in hooks:
            hooks = {**hooks, event: groups}
    result["hooks"] = hooks
    return result


def plan(repo: Path, kitchen: Path) -> list[tuple[str, bytes, int]]:
    """(repo-relative path, bytes, mode) for every file that differs from what this kitchen would write."""
    files = kitchen_files(kitchen)
    names = [n for n, (_, mode) in files.items() if mode == 0o755]
    wanted = [(f"{VENDORED}/{n}", content, mode) for n, (content, mode) in files.items()]
    for tool in TOOLS:
        data = merged(load(repo / tool.settings), tool, names)
        wanted.append((tool.settings, (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode(), 0o644))
    changes = []
    for rel, content, mode in wanted:
        path = repo / rel
        for step in [path, *path.relative_to(repo).parents[:-1]]:
            if (repo / step).is_symlink():  # a linked .claude/ or .kitchen/ would carry the write out of the project
                raise GuardsError(f"{repo / step} is a symlink; kitchen guards writes only regular files inside the project")
        same = path.is_file() and path.read_bytes() == content
        if rel.startswith(VENDORED):
            same = same and (path.stat().st_mode & 0o777) == mode
        if not same:
            changes.append((rel, content, mode))
    return changes


def uncommitted(repo: Path, rels: list[str]) -> list[str]:
    """Paths among rels with content git does not hold yet: overwriting them would lose it for good."""
    result = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", *rels], capture_output=True, text=True)
    if result.returncode != 0:
        raise GuardsError(f"cannot read git status in {repo}: {result.stderr.strip()}")
    # an untracked copy that exists was written or edited by someone; the settings files are merged, so nothing in them is lost
    return [line[3:] for line in result.stdout.splitlines() if not line.startswith("??") or line[3:].startswith(VENDORED)]


def apply(repo: Path, changes: list[tuple[str, bytes, int]]) -> None:
    for rel, content, mode in changes:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # created new, never through a planted link
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.chmod(tmp, mode if rel.startswith(VENDORED) else (path.stat().st_mode & 0o777 if path.exists() else mode))
        os.replace(tmp, path)


def run(repo: Path, kitchen: Path, check: bool) -> int:
    if not (repo / ".git").exists():
        raise GuardsError(f"{repo} is not the root of a git repository")
    changes = plan(repo, kitchen)
    if check:
        for rel, _, _ in changes:
            print(f"FAIL  {rel} differs from what `kitchen guards` writes")
        print(f"{'FAIL' if changes else 'OK'}    kitchen guards in {repo}")
        return 1 if changes else 0
    dirty = uncommitted(repo, [rel for rel, _, _ in changes])
    if dirty:  # a committed file stays in history, so replacing it is reversible; an uncommitted edit is not
        raise GuardsError(f"uncommitted changes in {', '.join(dirty)}; commit or discard them first, so what kitchen guards replaces stays in git")
    apply(repo, changes)
    for rel, _, _ in changes:
        print(f"wrote      {rel}")
    guards, after_compact = split([n for n, (_, m) in kitchen_files(kitchen).items() if m == 0o755])
    print(f"OK    guards in {repo}: {', '.join(guards)}; after a compaction: {', '.join(after_compact) or 'none'}"
          + ("" if changes else " (already current)"))
    print("next:  commit them; Claude Code loads them in a session started at the repo root; "
          "in Codex, trust the project and open /hooks once to approve them (Codex skips a new or changed hook until then)")
    return 0
