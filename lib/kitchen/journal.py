"""`kitchen log`: checkpoints that autonomous agents leave for `kitchen status`.

Principles: `handoff`, `owner-attention` (blocked and decision notify)."""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import notify
from .common import git_common_dir, git_out, now, parse_ts, state_dir

STATUSES = ("note", "done", "blocked", "decision")
OWNER_STATUSES = ("blocked", "decision")  # checkpoints that wait on the owner: notified, and exceptions in status


def journal_path() -> Path:
    return state_dir() / "log.jsonl"


def detect_agent() -> str:
    if os.environ.get("KITCHEN_AGENT"):
        return os.environ["KITCHEN_AGENT"]
    if os.environ.get("CLAUDECODE"):
        return "claude"
    if any(name.startswith("CODEX_") for name in os.environ):
        return "codex"
    return "unknown"


def detect_session(agent: str) -> str | None:
    """The agent's own session id: Claude Code and Codex each export one (measured in T3 Code and the CLIs)."""
    return os.environ.get({"claude": "CLAUDE_CODE_SESSION_ID", "codex": "CODEX_SESSION_ID"}.get(agent, ""))


def write(message: str, status: str, cwd: Path) -> dict:
    repo = git_out(cwd, "rev-parse", "--show-toplevel")
    entry = {
        "ts": now().isoformat(timespec="seconds"),
        "repo": repo or str(cwd.resolve()),
        "project": git_common_dir(cwd) if repo else None,  # worktrees of one repo share it
        "branch": git_out(cwd, "rev-parse", "--abbrev-ref", "HEAD") if repo else None,
        "sha": git_out(cwd, "rev-parse", "--short", "HEAD") if repo else None,
        "agent": (agent := detect_agent()),
        "session": detect_session(agent),
        "status": status,
        "message": message,
    }
    return append(entry)


def append(entry: dict) -> dict:
    """One entry at the end of the journal; also for jobs that log on a project's behalf (automation/bin/daily-audit)."""
    journal_path().parent.mkdir(parents=True, exist_ok=True)
    with journal_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def notify_owner(entry: dict) -> str | None:
    """A local notification for a checkpoint that waits on the owner. Returns why it was not shown, if it was not."""
    if entry["status"] not in OWNER_STATUSES:
        return None
    code, text = notify.notify(f"kitchen: {entry['status']} in {Path(entry['repo']).name}", f"[{entry['agent']}] {entry['message']}")
    return None if code == notify.SHOWN else text


def belongs(entry: dict, project: str | None, repo: str | None) -> bool:
    """An entry belongs to a repo by git common dir, so checkpoints logged from a worktree roll up.
    Entries written before the common dir was recorded fall back to their toplevel path."""
    if entry.get("project"):
        return entry["project"] == project
    return entry.get("repo") == repo


def read(since=None, project: str | None = None, repo: str | None = None) -> list[dict]:
    if not journal_path().is_file():
        return []
    entries = []
    for number, line in enumerate(journal_path().read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"{journal_path()}:{number} is not valid JSON: {error}") from error
        ts = parse_ts(str(entry.get("ts", "")))
        if since and ts is not None and ts < since:
            continue  # an entry whose time cannot be read cannot be placed outside the window: it is kept
        if (project or repo) and not belongs(entry, project, repo):
            continue
        entries.append(entry)
    return entries
