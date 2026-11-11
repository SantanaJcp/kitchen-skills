"""Shared helpers: paths, git, durations."""
from __future__ import annotations

import datetime
import os
import re
import subprocess
from pathlib import Path


def home() -> Path:
    return Path(os.environ["HOME"])


def state_dir() -> Path:
    return Path(os.environ.get("KITCHEN_STATE") or home() / ".local" / "state" / "kitchen")


def config_dir() -> Path:
    return Path(os.environ.get("KITCHEN_CONFIG") or home() / ".config" / "kitchen")


def denylist_path() -> Path:
    return Path(os.environ.get("KITCHEN_DENYLIST") or home() / ".config" / "kitchen" / "denylist.txt")


def denylist_terms() -> list[str] | None:
    """The private terms of the denylist, one per non-comment line; None when there is no denylist."""
    path = denylist_path()
    if not path.is_file():
        return None
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]


def term_pattern(term: str) -> re.Pattern:
    """Smart case, whole words: the name "Acme" must not match "the acme of design"; "acme" matches any case."""
    return re.compile(rf"\b{re.escape(term)}\b", 0 if term != term.lower() else re.IGNORECASE)


def private_terms() -> list[re.Pattern] | None:
    """Patterns for the private terms in the denylist; None when there is no denylist."""
    terms = denylist_terms()
    return None if terms is None else [term_pattern(term) for term in terms]


def projects() -> list[Path]:
    """Projects listed one absolute path per line in ~/.config/kitchen/projects.txt."""
    listing = config_dir() / "projects.txt"
    if not listing.is_file():
        return []
    lines = (line.strip() for line in listing.read_text(encoding="utf-8").splitlines())
    return [Path(os.path.expanduser(line)) for line in lines if line and not line.startswith("#")]


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def git_out(repo: Path, *args: str) -> str | None:
    result = git(repo, *args)
    return result.stdout.strip() if result.returncode == 0 else None


def git_common_dir(path: Path) -> str | None:
    """The repo a path belongs to, shared by all its worktrees: the resolved git common dir."""
    out = git_out(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return str(Path(out).resolve()) if out else None


def now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_since(value: str) -> datetime.datetime:
    match = re.fullmatch(r"(\d+)([hd])", value)
    if not match:
        raise ValueError(f"--since must look like 24h or 7d, got {value!r}")
    amount, unit = int(match.group(1)), match.group(2)
    return now() - datetime.timedelta(hours=amount if unit == "h" else amount * 24)


def parse_ts(value: str) -> datetime.datetime | None:
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)
