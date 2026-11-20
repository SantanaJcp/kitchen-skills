"""`kitchen guards <repo>`: the kitchen's hooks in one project, so they run there and nowhere else.

Copies hooks/ into the repo's .kitchen/hooks/ (or calls hooks/ directly inside the kitchen repo itself) and wires ONE
PreToolUse Bash handler (`kitchen-guard`, behind a POSIX-sh prefilter that skips Python unless the payload mentions
git/rm/gh) plus `chef-mode-after-compact` on SessionStart `compact`, in `.claude/settings.json` and `.codex/hooks.json`.
Re-running on a repo that carried the old five guards replaces those handlers and removes the old vendored files it
managed, leaving any project-own hooks untouched.

Principles: `doors` (the guard), `no-fallbacks` (fail closed, refuse what cannot be parsed), `isolate` (one project,
reruns byte-identical), `encode-lessons` (rules that became one fast guard).
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
GUARD = "kitchen-guard"
AFTER_COMPACT = "chef-mode-after-compact"
# Old vendored files replaced by single `kitchen-guard`; removed when re-running `kitchen guards`.
LEGACY_VENDORED = (
    "deny-no-verify",
    "deny-private-publish",
    "deny-recursive-rm",
    "deny-shared-push",
    "deny-unslotted-heavy",
    "shellparse.py",
)


class GuardsError(Exception):
    pass


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

# POSIX-sh prefilter: exits 0 without starting Python when a valid command payload mentions none of git/rm/gh.
# Payloads missing "command" (e.g. malformed JSON) still pass through to kitchen-guard so it fails closed.
PREFILTER = (
    'p=$(cat) || exit 2; '
    'case "$p" in *\\"command\\"*) '
    'case "$p" in '
    '*[!A-Za-z0-9_]git[!A-Za-z0-9_]*|*[!A-Za-z0-9_]rm[!A-Za-z0-9_]*|*[!A-Za-z0-9_]gh[!A-Za-z0-9_]*'
    '|*\\\\[nt]git[!A-Za-z0-9_]*|*\\\\[nt]rm[!A-Za-z0-9_]*|*\\\\[nt]gh[!A-Za-z0-9_]*) ;; '
    '*) exit 0 ;; esac ;; esac; '
    'printf "%s" "$p" | "$h"'
)


def kitchen_files(kitchen: Path) -> dict[str, tuple[bytes, int]]:
    """name -> (bytes, mode) for every executable hook in hooks/."""
    folder = kitchen / "hooks"
    out = {}
    for path in sorted(folder.iterdir()) if folder.is_dir() else []:
        if path.is_file() and "." not in path.name and os.access(path, os.X_OK):
            out[path.name] = (path.read_bytes(), 0o755)
    if GUARD not in out:
        raise GuardsError(f"this kitchen has no {GUARD} in {folder}")
    return out


def command(tool: Tool, hooks_rel: str, name: str) -> str:
    """Runs the hook; any exit other than 0 (allow) or 2 (block) blocks too. Bash guard uses the POSIX-sh prefilter."""
    invoke = PREFILTER if name == GUARD else '"$h"'
    return (f"sh -c '{tool.root}; s=\"$r/{tool.settings}\"; h=\"$r/{hooks_rel}/{name}\"; "
            f"[ -e \"$s\" ] || [ -L \"$s\" ] || exit 0; [ -x \"$h\" ] || {{ echo \"kitchen: guard missing: $h\" >&2; exit 2; }}; "
            f"{invoke}; c=$?; [ $c -eq 0 ] || [ $c -eq 2 ] || {{ echo \"kitchen: guard $h failed with exit $c\" >&2; exit 2; }}; exit $c'")


# Handlers this command writes (both vendored and direct hooks/ in the kitchen repo) plus older forms.
MANAGED = (f'h="$r/{VENDORED}/', f'h="$CLAUDE_PROJECT_DIR/{VENDORED}/', 'h="$r/hooks/')


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


def merged(data: dict, tool: Tool, hooks_rel: str, names: list[str]) -> dict:
    """data with the kitchen's handlers replaced by one PreToolUse and one SessionStart handler; nothing else changes."""
    result = copy.deepcopy(data)
    hooks = result.get("hooks", {})
    guards = [n for n in names if n != AFTER_COMPACT]
    after_compact = [n for n in names if n == AFTER_COMPACT]
    for event, matcher, mine in (("PreToolUse", tool.matcher, guards), ("SessionStart", tool.compact, after_compact)):
        groups = []
        for group in hooks.get(event, []):
            kept = [h for h in group.get("hooks", []) if not is_kitchen_handler(h)]
            if kept or not group.get("hooks"):
                groups.append({**group, "hooks": kept})
        if mine:
            groups.append({"matcher": matcher, "hooks": [
                {"type": "command", "command": command(tool, hooks_rel, name), "timeout": TIMEOUT_SECONDS} for name in mine]})
        if groups or event in hooks:
            hooks = {**hooks, event: groups}
    result["hooks"] = hooks
    return result


def _is_kitchen_repo(repo: Path, kitchen: Path) -> bool:
    return repo.resolve() == kitchen.resolve() or (
        (repo / "bin" / "kitchen").is_file()
        and (repo / "lib" / "kitchen" / "common.py").is_file()
        and (repo / "hooks" / GUARD).is_file()
    )


def plan(repo: Path, kitchen: Path) -> tuple[list[tuple[str, bytes, int]], list[str]]:
    """(writes, removals): files to write/update and legacy vendored files to delete."""
    self_repo = _is_kitchen_repo(repo, kitchen)
    hooks_rel = "hooks" if self_repo else VENDORED
    files = kitchen_files(kitchen)
    names = list(files)
    wanted: list[tuple[str, bytes, int]] = []
    if not self_repo:
        wanted += [(f"{VENDORED}/{n}", content, mode) for n, (content, mode) in files.items()]
    for tool in TOOLS:
        data = merged(load(repo / tool.settings), tool, hooks_rel, names)
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
    stale_names = (*LEGACY_VENDORED, *names) if self_repo else LEGACY_VENDORED
    removals = [f"{VENDORED}/{n}" for n in stale_names if (repo / VENDORED / n).exists() or (repo / VENDORED / n).is_symlink()]
    return changes, removals


def uncommitted(repo: Path, rels: list[str]) -> list[str]:
    """Paths among rels with content git does not hold yet: overwriting them would lose it for good."""
    if not rels:
        return []
    result = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", *rels], capture_output=True, text=True)
    if result.returncode != 0:
        raise GuardsError(f"cannot read git status in {repo}: {result.stderr.strip()}")
    return [line[3:] for line in result.stdout.splitlines() if not line.startswith("??") or line[3:].startswith(VENDORED)]


def apply(repo: Path, changes: list[tuple[str, bytes, int]], removals: list[str]) -> None:
    for rel, content, mode in changes:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")  # created new, never through a planted link
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
        os.chmod(tmp, mode if rel.startswith(VENDORED) else (path.stat().st_mode & 0o777 if path.exists() else mode))
        os.replace(tmp, path)
    for rel in removals:
        (repo / rel).unlink()
    # Clean up empty .kitchen/hooks and .kitchen if nothing else lives there.
    for folder in (repo / VENDORED, repo / ".kitchen"):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


def run(repo: Path, kitchen: Path, check: bool) -> int:
    if not (repo / ".git").exists():
        raise GuardsError(f"{repo} is not the root of a git repository")
    changes, removals = plan(repo, kitchen)
    if check:
        for rel, _, _ in changes:
            print(f"FAIL  {rel} differs from what `kitchen guards` writes")
        for rel in removals:
            print(f"FAIL  {rel} is an old kitchen guard file; rerun `kitchen guards` to remove it")
        print(f"{'FAIL' if changes or removals else 'OK'}    kitchen guards in {repo}")
        return 1 if changes or removals else 0
    dirty = uncommitted(repo, [rel for rel, _, _ in changes] + removals)
    if dirty:  # a committed file stays in history, so replacing it is reversible; an uncommitted edit is not
        raise GuardsError(f"uncommitted changes in {', '.join(dirty)}; commit or discard them first, so what kitchen guards replaces stays in git")
    apply(repo, changes, removals)
    for rel, _, _ in changes:
        print(f"wrote      {rel}")
    for rel in removals:
        print(f"removed    {rel}")
    print(f"OK    guards in {repo}: {GUARD}; after a compaction: {AFTER_COMPACT}"
          + ("" if changes or removals else " (already current)"))
    print("next:  commit them; Claude Code loads them in a session started at the repo root; "
          "in Codex, trust the project and open /hooks once to approve them (Codex skips a new or changed hook until then)")
    return 0
