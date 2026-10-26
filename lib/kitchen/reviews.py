"""`kitchen review-round`: how many review rounds a branch has had, and whether a third one is the owner's to grant.

Chef mode gives a PR two rounds: one open review, then one pass that only reruns the previous round's repros against
the fixes. A round is a head that no earlier round reviewed: a run on the head of any recorded round is a retry of that
round, and counts nothing and grants nothing.

The count is one JSON file per (project, branch) under the state dir. The project is the repository's identity:
- a repo with an `origin` remote: the normalized URL of origin. The scheme, the user (and anything in it), a trailing
  `.git` and a trailing slash are dropped, and `git@host:owner/repo` becomes `host/owner/repo`. Every worktree and every
  clone of that remote shares the count.
- a repo without an `origin` remote (the identity rule for local-only repos): its main worktree path. Every worktree of
  it shares the count; another checkout of it does not.

Past two rounds only a grant allows another: the owner's own words, recorded with --owner-approved. Each grant allows
one round. A grant that no round has used yet (the record holds more grants than rounds beyond two) allows the next round
without a second grant being stacked, and its words are not recorded again. A refused round writes nothing.

Every change to a record happens under an exclusive lock on `<record>.lock`, in one save, so a grant and the round it
allows are durable together. --show reads without the lock: a save replaces the record whole, so a read sees either the
old record or the new one. A damaged record (a round without a full commit SHA, a grant without text) is refused, never counted.

Principles: `cross-review` (the round budget), `owner-attention` (a refused round is one numbered decision for the owner).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kitchen import common

BASE_ROUNDS = 2
RERUN = "rerun only the previous round's repros against the fixes; P2 and lower become tests, not another round"
SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")  # SHA-1 or SHA-256 object names


class ReviewError(Exception):
    """A request refused outright: nothing is recorded."""


def short(sha: str) -> str:
    return sha[:7]


def origin_identity(url: str) -> str:
    """The identity a remote URL names: see the module docstring. A `file://` URL keeps its path."""
    value = url.strip()
    if "://" in value:
        authority, slash, rest = value.split("://", 1)[1].partition("/")
        value = authority.rsplit("@", 1)[-1] + slash + rest
    else:
        host, colon, path = value.partition(":")
        if colon and "/" not in host:  # scp-like: [user@]host:path
            value = host.rsplit("@", 1)[-1] + "/" + path
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[: -len(".git")].rstrip("/")
    return value


def project_of(repo: Path) -> str:
    """The repository's identity (module docstring): the normalized origin URL, else the main worktree path."""
    common_dir = common.git_common_dir(repo)
    if common_dir is None:
        raise ReviewError(f"{repo} is not inside a git repository")
    origin = common.git_out(repo, "config", "--get", "remote.origin.url")
    if origin is None:
        return str(Path(common_dir).parent)
    identity = origin_identity(origin)
    if not identity:
        raise ReviewError("origin's URL names no repository once its scheme, user and .git are dropped; fix the remote")
    return identity


def branch_of(repo: Path, branch: str | None) -> str:
    if branch is None:
        current = common.git_out(repo, "symbolic-ref", "-q", "--short", "HEAD")
        if current is None:
            raise ReviewError("HEAD is detached: pass --branch NAME, the branch this review is for")
        return current
    if not branch:
        raise ReviewError("--branch is empty")
    return branch


def head_of(repo: Path, ref: str) -> str:
    sha = common.git_out(repo, "rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
    if not sha:
        raise ReviewError(f"{ref} is not a commit in {repo}")
    return sha


def record_path(project: str, branch: str) -> Path:
    digest = hashlib.sha256(json.dumps([project, branch]).encode("utf-8")).hexdigest()[:16]
    slug = re.sub(r"[^a-z0-9]+", "-", f"{Path(project).name}-{branch}".lower()).strip("-")[:48] or "review"
    return common.state_dir() / "reviews" / f"{slug}-{digest}.json"


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """An exclusive lock on the record's `.lock` file for the whole block; closing the file releases it. The file stays."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def damage(record: object) -> str | None:
    """Why a record cannot be counted, or None. Every round and every grant must be well formed."""
    if not isinstance(record, dict) or not isinstance(record.get("rounds"), list) or not isinstance(record.get("grants"), list):
        return "it has no rounds and grants lists"
    for number, item in enumerate(record["rounds"], start=1):
        if not (isinstance(item, dict) and isinstance(item.get("head"), str) and SHA.fullmatch(item["head"])
                and isinstance(item.get("ts"), str)):
            return f"round {number} is not a full commit SHA with a ts"
    for number, item in enumerate(record["grants"], start=1):
        if not (isinstance(item, dict) and isinstance(item.get("ts"), str) and isinstance(item.get("text"), str)
                and item["text"].strip()):
            return f"grant {number} is not a ts with a non-empty text"
    return None


def load(path: Path, project: str, branch: str) -> dict:
    if not path.is_file():
        return {"project": project, "branch": branch, "rounds": [], "grants": []}
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReviewError(f"cannot read {path}: {error}; fix it by hand, a damaged count is never reset silently") from error
    problem = damage(record)
    if problem is not None:
        raise ReviewError(f"{path} is damaged: {problem}; fix it by hand, a damaged count is never reset silently")
    return record


def save(path: Path, record: dict) -> None:
    """Replace the record in one rename: a unique temp file beside it, synced, then os.replace over the record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=f"{path.name}.", suffix=".tmp", delete=False, encoding="utf-8")
    temp = Path(handle.name)
    try:
        with handle:
            handle.write(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def pause_for_tests() -> None:
    """Test seam: KITCHEN_REVIEW_HOLD seconds between the check and the save, so a test can widen the race window. Unset in use."""
    seconds = os.environ.get("KITCHEN_REVIEW_HOLD")
    if seconds:
        time.sleep(float(seconds))


def round_line(number: int, allowed: int, branch: str, head: str) -> str:
    what = "open review" if number == 1 else RERUN
    return f"round {number} of {allowed} on {branch} ({short(head)}): {what}"


def blocked_line(branch: str, rounds: list[dict]) -> str:
    shas = ", ".join(short(r["head"]) for r in rounds)
    return (f"BLOCKED: review budget spent on {branch}: {len(rounds)} rounds ({shas}). "
            f"Decision for the owner: one more round on {branch}? yes runs one rerun-only round; "
            "no merges on the current findings if none is P0/P1, else stops and reports them.")


def describe(branch: str, project: str, record: dict) -> str:
    rounds, grants = record["rounds"], record["grants"]
    allowed = BASE_ROUNDS + len(grants)
    lines = [f"{branch} in {project}: {len(rounds)} of {allowed} rounds used ({BASE_ROUNDS} base + {len(grants)} owner grants)"]
    lines += [f"  round {number}  {short(r['head'])}  {r['ts']}" for number, r in enumerate(rounds, start=1)]
    lines += [f"  grant {g['ts']}  {json.dumps(g['text'], ensure_ascii=False)}" for g in grants]
    return "\n".join(lines)


def run(repo: Path, branch: str | None, ref: str | None, owner_approved: str | None, show: bool) -> tuple[bool, str]:
    """(ok, text). ok is False when the round is BLOCKED; text goes to stdout when ok, to stderr when not.

    Raises ReviewError for a refused request, before anything is written.
    """
    project = project_of(repo)
    name = branch_of(repo, branch)
    path = record_path(project, name)
    if show:
        return True, describe(name, project, load(path, project, name))
    granted = owner_approved is not None
    text = owner_approved.strip() if granted else ""
    if granted and not text:
        raise ReviewError("--owner-approved needs the owner's own words; empty text is refused")
    head = head_of(repo, ref if ref is not None else name)
    stamp = common.now().isoformat(timespec="seconds")
    with locked(path):
        record = load(path, project, name)
        rounds, grants = record["rounds"], record["grants"]
        earlier = next((number for number, r in enumerate(rounds, start=1) if r["head"] == head), None)
        if earlier is not None:  # a head any round reviewed is a retry: it counts nothing and grants nothing
            return True, round_line(earlier, BASE_ROUNDS + len(grants), name, head)
        if granted and len(grants) <= max(0, len(rounds) - BASE_ROUNDS):  # no unused grant: this one allows the next round
            grants.append({"ts": stamp, "text": text})
        allowed = BASE_ROUNDS + len(grants)
        if len(rounds) >= allowed:
            return False, blocked_line(name, rounds)
        rounds.append({"head": head, "ts": stamp})
        pause_for_tests()
        save(path, record)  # the grant and the round it allows, in one save
    return True, round_line(len(rounds), allowed, name, head)
