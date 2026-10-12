"""`kitchen status`: where every project stands, read from git, gh, the nightly guard, the gardener's and the daily
audit's records and the journal. A fact it cannot read is `unknown`, never zero or none.
Principles: `truthful-state`, `owner-attention` (--exceptions), `handoff` (owed decisions, checkpoints).

`--exceptions` keeps only the lines that are not green: a project it cannot read, a nightly that is not green or is
overdue, a gardener run that was refused, incomplete, overdue or not recorded on this machine, a daily audit that
ended findings, blocked, refused or incomplete, is overdue or has no record, PRs or decisions it
could not read, owed decisions, and checkpoints that wait on the owner (blocked, decision), unattached ones included.

Each of nightly, gardener, audit and decisions is configured only by explicit config, never guessed from the records:
- the nightly, by at least one `GUARD_STEPS` step in the project's automation env, `<config>/automation/<project>.env`
  (a gardener-only host sets `GUARD_STEPS=()`);
- the gardener, by at least one `GARDENER_VERIFY_STEPS` step in that env, or a `gardener` key in the project's
  integrate.toml entry;
- the audit, by at least one `AUDIT_GUARD_STEPS` step in that env, which the job requires (a host that does not run it
  sets `AUDIT_GUARD_STEPS=()`);
- decisions, by a `base` in that entry.
One that is not configured shows an informational `not configured` line: never green, never an exception. One that is
configured and lacks its record is an exception; a config that cannot be read is `unknown`, also an exception.

Status reads the env the way the jobs do (automation/lib/common.sh): it sources it, so whatever the env runs, status
runs too. It is the owner's private config, which the jobs already execute every night. bash sources it under
`set -euo pipefail` in an environment like launchd's (HOME, USER, LOGNAME, TMPDIR, PATH=/usr/bin:/bin), with no stdin,
its output discarded and a deadline (KITCHEN_ENV_TIMEOUT_SECONDS, default 5). A non-zero exit or the deadline is
`unknown`, never `not configured`.

A project whose gardener or nightly runs on another host says so in integrate.toml, `gardener = "remote:<host-label>"`
or `nightly = "remote:<host-label>"`: that line is then informational, `remote (<host-label>): not read here`. Never
green, never an exception.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import signal
import subprocess
import tempfile
import tomllib
from pathlib import Path

from . import integrate, journal
from .common import config_dir, git, git_common_dir, git_out, home, now, parse_ts, state_dir

OWED_DECISION = re.compile(r"^\s*- \[ \]", re.MULTILINE)
UNKNOWN = "unknown"  # a read failed: never shown as zero or none
NIGHTLY_CADENCE = datetime.timedelta(hours=24)  # install-schedule runs the guard daily
GARDENER_DUE = datetime.timedelta(days=8)  # install-schedule runs the gardener weekly; plus a day of grace
# A night runs from noon to noon, local time: a manual run at 23:00 and the scheduled 02:00 run are one night.
NOON = datetime.timedelta(hours=12)
GARDENER_GREEN = ("published", "none")
# install-schedule --audit runs it daily with no catch-up, plus a day of grace as for the gardener: its record's time
# is the run's end, which moves by up to its timeout and the lock wait, and one missed 04:00 waits a whole day.
AUDIT_DUE = datetime.timedelta(days=2)
AUDIT_GREEN = ("clean", "changed", "skipped")  # the pass's good outcomes; findings, blocked, refused, incomplete are not
REMOTE = re.compile(r"remote:(\S+)")

# What the jobs see of the env: they source it under these options (automation/lib/common.sh). Its own output is
# discarded so that only the counts reach stdout; an unset array counts 0.
ENV_PROBE = ('set -euo pipefail\nsource "$1" >/dev/null 2>&1\nset +u\n'
             'printf "%s %s %s\\n" "${#GUARD_STEPS[@]}" "${#GARDENER_VERIFY_STEPS[@]}" "${#AUDIT_GUARD_STEPS[@]}"\n')
ENV_PATH = "/usr/bin:/bin"  # launchd's default PATH, where the scheduled jobs find bash

def gh_timeout() -> int:
    return int(os.environ.get("KITCHEN_GH_TIMEOUT_SECONDS") or 15)


def env_timeout() -> int:
    return int(os.environ.get("KITCHEN_ENV_TIMEOUT_SECONDS") or 5)


def history(kind: str, name: str) -> list[dict]:
    path = state_dir() / kind / f"{name}.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def age_seconds(run: dict) -> int | None:
    ts = parse_ts(run.get("ts") or "")
    return int((now() - ts).total_seconds()) if ts else None


def night(run: dict) -> datetime.date | None:
    ts = parse_ts(run.get("ts") or "")
    return (ts.astimezone() - NOON).date() if ts else None


def automation_env(name: str) -> dict:
    """The project's automation env, as the jobs find and source it (automation/lib/common.sh): how many guard,
    gardener verify and audit guard steps it leaves, or why it could not be read. No file means no automation
    configured."""
    path = config_dir() / "automation" / f"{name}.env"
    if not path.exists():
        return {"path": path, "exists": False, "guard": 0, "gardener": 0, "audit": 0, "error": None}
    try:
        path.read_bytes()
    except OSError as error:
        return {"path": path, "exists": True, "error": f"cannot read {path}: {error}"}
    counts, error = source_env(path)
    if error:
        return {"path": path, "exists": True, "error": error}
    return {"path": path, "exists": True, "guard": counts[0], "gardener": counts[1], "audit": counts[2], "error": None}


def source_env(path: Path) -> tuple[tuple[int, int, int] | None, str | None]:
    """Source the env in bash as the jobs do and return its GUARD_STEPS, GARDENER_VERIFY_STEPS and AUDIT_GUARD_STEPS
    counts, or why not."""
    timeout = env_timeout()
    env = {"HOME": str(home()), "PATH": ENV_PATH, **{k: os.environ[k] for k in ("USER", "LOGNAME", "TMPDIR") if k in os.environ}}
    with tempfile.TemporaryFile() as out:  # a file, not a pipe: a child the env left behind cannot block the read
        try:
            proc = subprocess.Popen(["bash", "--noprofile", "--norc", "-c", ENV_PROBE, "kitchen-status", str(path)],
                                    stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL, env=env, cwd="/",
                                    start_new_session=True)
        except FileNotFoundError:
            return None, f"cannot source {path}: no bash on {ENV_PATH}"
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)  # the whole group: the env's own children too
            except ProcessLookupError:
                pass
            proc.wait()
            return None, f"sourcing {path} took longer than {timeout}s"
        out.seek(0)
        printed = out.read().decode("utf-8", errors="replace")
    if code != 0:
        return None, f"cannot source {path}: bash exited {code}"
    counts = re.fullmatch(r"(\d+) (\d+) (\d+)\n", printed)
    if not counts:
        return None, f"sourcing {path} printed {printed!r}, not the step counts"
    return (int(counts.group(1)), int(counts.group(2)), int(counts.group(3))), None


def nightly(name: str) -> dict | None:
    runs = history("nightly", name)
    if not runs:
        return None
    streak = []
    for run in reversed(runs):
        if run.get("status") != "green":
            break
        streak.append(run)
    nights = {night(run) for run in streak}
    age = age_seconds(runs[-1])
    return {**runs[-1], "green_streak": UNKNOWN if None in nights else len(nights), "age_seconds": age,
            "overdue": UNKNOWN if age is None else age > NIGHTLY_CADENCE.total_seconds()}


def remote_setting(key: str, entry: dict) -> dict | None:
    """{"remote": host} when the project's entry says this job runs on another host, a setting error when it says so
    wrongly, None when it does not say."""
    value = entry.get(key)
    if value is None:
        return None
    remote = REMOTE.fullmatch(value) if isinstance(value, str) else None
    if not remote:
        return {"setting_error": f"{key} = {value!r} is not remote:<host-label> ({integrate.config_path()})"}
    return {"remote": remote.group(1)}


def gardener(name: str, entry: dict, error: str | None, env: dict) -> dict:
    """The gardener's last run as recorded on this machine, where it runs when that is another host, or that it is
    not configured for this project."""
    if error:
        return {"setting_error": error}
    remote = remote_setting("gardener", entry)
    if remote is not None:
        return remote
    if not env["exists"]:
        return {"not_configured": f"no {env['path']} and no gardener in {integrate.config_path()}"}
    if env["error"]:
        return {"setting_error": env["error"]}
    if not env["gardener"]:
        return {"not_configured": f"GARDENER_VERIFY_STEPS is empty or unset in {env['path']} and no gardener in {integrate.config_path()}"}
    runs = history("gardener", name)
    if not runs:
        return {"no_record": f"GARDENER_VERIFY_STEPS in {env['path']}"}
    age = age_seconds(runs[-1])
    return {**runs[-1], "age_seconds": age, "overdue": UNKNOWN if age is None else age > GARDENER_DUE.total_seconds()}


def audit(name: str, env: dict) -> dict:
    """The daily audit's last run as recorded on this machine, or that it is not configured for this project. Only the
    automation env configures it: integrate.toml has no audit key."""
    if not env["exists"]:
        return {"not_configured": f"no {env['path']}"}
    if env["error"]:
        return {"setting_error": env["error"]}
    if not env["audit"]:
        return {"not_configured": f"AUDIT_GUARD_STEPS is empty or unset in {env['path']}"}
    runs = history("audit", name)
    if not runs:
        return {"no_record": f"AUDIT_GUARD_STEPS in {env['path']}"}
    age = age_seconds(runs[-1])
    return {**runs[-1], "age_seconds": age, "overdue": UNKNOWN if age is None else age > AUDIT_DUE.total_seconds()}


def pull_requests(repo: Path) -> dict:
    timeout = gh_timeout()
    try:
        proc = subprocess.Popen(
            ["gh", "pr", "list", "--author", "@me", "--state", "open", "--json", "number,title,isDraft,headRefName"],
            cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
    except FileNotFoundError:
        return {"error": "gh is not installed"}
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # the whole group: a child holding the pipes would block the read
        except ProcessLookupError:
            pass
        proc.communicate()
        return {"error": f"gh timed out after {timeout}s"}
    if proc.returncode != 0:
        return {"error": (err.strip().splitlines() or ["gh failed"])[0]}
    try:
        return {"open": json.loads(out or "[]")}
    except json.JSONDecodeError:
        return {"error": "gh printed something that is not JSON"}


def upstream(repo: Path, branch: str | None) -> dict | str | None:
    """None when the branch has no upstream; UNKNOWN when git could not say."""
    if branch is None:
        return UNKNOWN
    if branch == "HEAD":
        return None  # detached
    tracked = git(repo, "for-each-ref", "--format=%(upstream:short)", f"refs/heads/{branch}")
    if tracked.returncode != 0:
        return UNKNOWN
    if not tracked.stdout.strip():
        return None
    counts = git_out(repo, "rev-list", "--left-right", "--count", f"{tracked.stdout.strip()}...HEAD")
    if not counts:
        return UNKNOWN
    behind, ahead = (int(n) for n in counts.split())
    return {"ahead": ahead, "behind": behind, "sha": git_out(repo, "rev-parse", f"{tracked.stdout.strip()}^{{commit}}")}


def nightly_match(run: dict | None, head: str | None, up) -> str | None:
    """Which local commit the nightly run tested, compared by full SHA; short SHAs are never matched."""
    sha = (run or {}).get("sha") or ""
    if len(sha) != 40:
        return None
    if sha == head:
        return "HEAD"
    if isinstance(up, dict) and sha == up.get("sha"):
        return "upstream"
    return None


def settings(name: str) -> tuple[dict, str | None]:
    """The project's entry in integrate.toml, or why it could not be read."""
    path = integrate.config_path()
    if not path.is_file():
        return {}, None
    try:
        return tomllib.loads(path.read_text(encoding="utf-8")).get("projects", {}).get(name, {}), None
    except (tomllib.TOMLDecodeError, OSError) as error:
        return {}, f"cannot read {path}: {error}"


def base_of(name: str, entry: dict, error: str | None) -> dict:
    """The project's base ref from integrate.toml. There is no default base: without one, decisions are not configured."""
    if error:
        return {"ref": None, "sha": None, "error": error, "configured": True}  # unknown, not absent
    ref = entry.get("base")
    if not ref:
        return {"ref": None, "sha": None, "error": f"no base for {name!r} in {integrate.config_path()}", "configured": False}
    return {"ref": ref, "sha": None, "error": None, "configured": True}


def resolve_base(repo: Path, base: dict) -> dict:
    if base["error"]:
        return base
    sha = git_out(repo, "rev-parse", "--verify", "--quiet", f"{base['ref']}^{{commit}}")
    return {**base, "sha": sha} if sha else {**base, "error": f"cannot resolve {base['ref']} in this repo"}


def decisions(repo: Path, base: dict) -> dict:
    """decisions.md as committed on the base ref, not as it is in whatever the checkout holds."""
    if not base["configured"]:
        return {"ref": None, "not_configured": base["error"]}
    if base["error"]:
        return {"ref": base["ref"], "owed": UNKNOWN, "error": base["error"]}
    listing = git(repo, "ls-tree", "--name-only", base["sha"], "--", "decisions.md")
    if listing.returncode != 0:
        return {"ref": base["ref"], "owed": UNKNOWN, "error": f"cannot list {base['ref']}"}
    if not listing.stdout.strip():
        return {"ref": base["ref"], "sha": base["sha"], "owed": None}
    shown = git(repo, "show", f"{base['sha']}:decisions.md")
    if shown.returncode != 0:
        return {"ref": base["ref"], "owed": UNKNOWN, "error": f"cannot read decisions.md at {base['ref']}"}
    return {"ref": base["ref"], "sha": base["sha"], "owed": len(OWED_DECISION.findall(shown.stdout))}


def behind_base(repo: Path, run: dict, base: dict) -> dict:
    """How many commits the base has that the nightly's SHA lacks."""
    sha = run.get("sha") or ""
    if base["error"]:
        return {"ref": base["ref"], "count": UNKNOWN, "why": base["error"]}
    if len(sha) != 40:
        return {"ref": base["ref"], "count": UNKNOWN, "why": "the nightly recorded no full SHA"}
    counted = git_out(repo, "rev-list", "--count", f"{sha}..{base['sha']}")
    if counted is None:
        return {"ref": base["ref"], "count": UNKNOWN, "why": "the nightly's SHA is not in this repo"}
    return {"ref": base["ref"], "count": int(counted)}


def project(repo: Path, since) -> dict:
    name = repo.name
    if not repo.is_dir():
        return {"name": name, "path": str(repo), "error": "path does not exist"}
    toplevel = git_out(repo, "rev-parse", "--show-toplevel")
    if not toplevel:
        return {"name": name, "path": str(repo), "error": "not a git repository"}

    branch = git_out(repo, "rev-parse", "--abbrev-ref", "HEAD")
    head = git_out(repo, "rev-parse", "HEAD")
    status = git(repo, "status", "--porcelain")
    listing = git(repo, "worktree", "list", "--porcelain")
    blocks = [b for b in listing.stdout.split("\n\n") if b.strip()][1:] if listing.returncode == 0 else None
    prunable = sum(1 for b in blocks if "\nprunable" in b) if blocks is not None else UNKNOWN
    up = upstream(repo, branch)
    env = automation_env(name)
    run = nightly(name)
    entry, config_error = settings(name)
    base = resolve_base(repo, base_of(name, entry, config_error))
    common_dir = git_common_dir(repo)

    return {
        "name": name,
        "path": toplevel,
        "git_common_dir": common_dir,
        "branch": branch or UNKNOWN,
        "sha": head or UNKNOWN,
        "dirty": len([line for line in status.stdout.splitlines() if line]) if status.returncode == 0 else UNKNOWN,
        "upstream": up,
        "extra_worktrees": len(blocks) - prunable if blocks is not None else UNKNOWN,
        "prunable_worktrees": prunable,
        "pull_requests": pull_requests(repo),
        "nightly": nightly_state(env, run, head, up, repo, base, entry, config_error),
        "gardener": gardener(name, entry, config_error, env),
        "audit": audit(name, env),
        "journal": journal.read(since=since, project=common_dir, repo=toplevel),
        "decisions": decisions(repo, base),
    }


def nightly_state(env: dict, run: dict | None, head: str | None, up, repo: Path, base: dict, entry: dict,
                  error: str | None) -> dict:
    """The nightly's last run, where it runs when that is another host, or that it is configured with no run recorded,
    or that it is not configured at all."""
    if error:
        return {"setting_error": error}
    remote = remote_setting("nightly", entry)
    if remote is not None:
        return remote
    if not env["exists"]:
        return {"not_configured": f"no {env['path']}"}
    if env["error"]:
        return {"setting_error": env["error"]}
    if not env["guard"]:
        return {"not_configured": f"GUARD_STEPS is empty or unset in {env['path']}"}
    if run is None:
        return {"no_record": f"GUARD_STEPS in {env['path']}"}
    return {**run, "matches": nightly_match(run, head, up), "behind": behind_base(repo, run, base)}


def build(repos: list[Path], since) -> dict:
    """Every project, plus the journal entries of the window that belong to none of them."""
    projects = [project(repo, since) for repo in repos]
    owners = [(p["git_common_dir"], p["path"]) for p in projects if "error" not in p]
    unattached = [e for e in journal.read(since=since) if not any(journal.belongs(e, common, top) for common, top in owners)]
    return {"projects": projects, "unattached": unattached}


def duration(seconds: int | None) -> str:
    if seconds is None:
        return UNKNOWN
    minutes = max(seconds, 0) // 60
    days, hours, minutes = minutes // 1440, minutes // 60 % 24, minutes % 60
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m" if hours else f"{minutes}m"


def header(p: dict) -> str:
    if "error" in p:
        return f"{p['name']}  ✗ {p['error']} ({p['path']})"
    up = p["upstream"]
    upstream_text = "no upstream" if up is None else (f"upstream {UNKNOWN}" if up == UNKNOWN else f"↑{up['ahead']} ↓{up['behind']}")
    return (f"{p['name']}  {p['branch']} @ {p['sha'][:8]}  {upstream_text}  dirty: {p['dirty']}  extra worktrees: {p['extra_worktrees']}"
            + (f" (+{p['prunable_worktrees']} prunable)" if p["prunable_worktrees"] not in (0, UNKNOWN) else ""))


def header_green(p: dict) -> bool:
    return "error" not in p and UNKNOWN not in (p["branch"], p["sha"], p["dirty"], p["extra_worktrees"], p["prunable_worktrees"], p["upstream"])


def nightly_line(run: dict) -> str:
    if "remote" in run:
        return f"nightly    remote ({run['remote']}): not read here"
    if "not_configured" in run:
        return f"nightly    not configured ({run['not_configured']})"
    if "no_record" in run:
        return f"nightly    ✗ no record ({run['no_record']})"
    if "setting_error" in run:
        return f"nightly    {UNKNOWN}: {run['setting_error']}"
    mark = "✓" if nightly_green(run) else "✗"  # an overdue green is not green now
    failed = f" (failed: {run['failed_step']})" if run.get("failed_step") else ""
    matches = f" (= {run['matches']})" if run.get("matches") else ""
    behind = run["behind"]
    behind_text = (f"{behind['count']} behind {behind['ref']}" if behind["count"] != UNKNOWN
                   else f"behind base: {UNKNOWN} ({behind['why']})")
    streak = run["green_streak"]
    streak_text = f"{streak} night{'' if streak == 1 else 's'}" if streak != UNKNOWN else UNKNOWN
    if run["overdue"] == UNKNOWN:
        overdue = f" · overdue: {UNKNOWN} (unreadable ts)"
    else:
        overdue = f" · overdue: last record {duration(run['age_seconds'])} ago, cadence 24h" if run["overdue"] else ""
    warnings = f" · warnings: {'; '.join(map(str, run['warnings']))}" if run.get("warnings") else ""
    return (f"nightly    {mark} {run.get('status')} at {run.get('ts')} on {str(run.get('sha'))[:8]}{matches}{failed}"
            f" · {behind_text} · green streak {streak_text}{overdue}{warnings}")


def nightly_green(run: dict) -> bool:
    """Green only with every fact known: a green verdict, not overdue, and a known distance to the base."""
    return run.get("status") == "green" and run["overdue"] is False and run["behind"]["count"] != UNKNOWN


def nightly_exception(run: dict) -> bool:
    """A nightly that is not configured, or runs on another host, is not read: neither green nor an exception."""
    return not nightly_green(run) and "not_configured" not in run and "remote" not in run


def gardener_line(run: dict) -> str:
    if "not_configured" in run:
        return f"gardener   not configured ({run['not_configured']})"
    if "no_record" in run:
        return (f"gardener   ✗ no record on this host ({run['no_record']}; if it runs on another host,"
                f" set gardener = \"remote:<host-label>\" in {integrate.config_path()})")
    if "setting_error" in run:
        return f"gardener   {UNKNOWN}: {run['setting_error']}"
    if "remote" in run:
        return f"gardener   remote ({run['remote']}): not read here"
    mark = "✓" if gardener_green(run) else "✗"
    detail = f": {run['detail']}" if run.get("detail") else ""
    warnings = f" · warnings: {'; '.join(map(str, run['warnings']))}" if run.get("warnings") else ""
    overdue = f" · overdue: last record {duration(run['age_seconds'])} ago, cadence weekly plus a day" if run["overdue"] is True else ""
    return f"gardener   {mark} {run.get('status')}{detail} · {duration(run['age_seconds'])} ago{overdue}{warnings}"


def gardener_green(run: dict) -> bool:
    """Green only for a good result whose time is known and that is not overdue (an unknown time shows as `unknown ago`)."""
    return run.get("status") in GARDENER_GREEN and run.get("overdue") is False


def gardener_exception(run: dict) -> bool:
    """A remote or unconfigured gardener is not read here: neither green nor an exception."""
    return not gardener_green(run) and "remote" not in run and "not_configured" not in run


def audit_line(run: dict) -> str:
    if "not_configured" in run:
        return f"audit      not configured ({run['not_configured']})"
    if "no_record" in run:
        return f"audit      ✗ no record ({run['no_record']})"
    if "setting_error" in run:
        return f"audit      {UNKNOWN}: {run['setting_error']}"
    mark = "✓" if audit_green(run) else "✗"
    on = f" on {run['sha'][:8]}" if run.get("sha") else ""
    detail = f": {run['detail']}" if run.get("detail") else ""
    warnings = f" · warnings: {'; '.join(map(str, run['warnings']))}" if run.get("warnings") else ""
    overdue = f" · overdue: last record {duration(run['age_seconds'])} ago, cadence daily plus a day" if run["overdue"] is True else ""
    return f"audit      {mark} {run.get('status')}{on}{detail} · {duration(run['age_seconds'])} ago{overdue}{warnings}"


def audit_green(run: dict) -> bool:
    """Green only for a good outcome whose time is known and that is not overdue (an unknown time shows as `unknown ago`)."""
    return run.get("status") in AUDIT_GREEN and run.get("overdue") is False


def audit_exception(run: dict) -> bool:
    """An audit that is not configured is not read: neither green nor an exception."""
    return not audit_green(run) and "not_configured" not in run


def prs_line(prs: dict) -> str:
    if "error" in prs:
        return f"PRs        {UNKNOWN}: {prs['error']}"
    listed = ", ".join(f"#{pr['number']}{' (draft)' if pr['isDraft'] else ''}" for pr in prs["open"]) or "none"
    return f"PRs        {listed}"


def decisions_line(d: dict) -> str:
    if "not_configured" in d:
        return f"decisions  not configured ({d['not_configured']})"
    if d["owed"] == UNKNOWN:
        return f"decisions  {UNKNOWN}: {d['error']}"
    if d["owed"] is None:
        return f"decisions  no decisions.md at {d['ref']} @ {d['sha'][:8]}"
    return f"decisions  {d['owed']} owed by you (read from {d['ref']} @ {d['sha'][:8]})"


def decisions_green(d: dict) -> bool:
    return "not_configured" not in d and d["owed"] != UNKNOWN and not d["owed"]


def decisions_exception(d: dict) -> bool:
    """Decisions without a base are not read: neither green nor an exception."""
    return not decisions_green(d) and "not_configured" not in d


def entry_line(entry: dict, with_repo: bool = False) -> str:
    where = f"{entry.get('repo')}: " if with_repo else ""
    when = str(entry.get("ts"))[:16] if parse_ts(str(entry.get("ts", ""))) else f"time {UNKNOWN} ({entry.get('ts')!r})"
    return f"{when} {entry['status']:<8} [{entry.get('agent')}] {where}{entry['message']}"


def journal_lines(label: str, entries: list[dict], with_repo: bool = False) -> list[str]:
    blocked = [e for e in entries if e.get("status") == "blocked"]
    unknown_time = sum(1 for e in entries if parse_ts(str(e.get("ts", ""))) is None)
    lines = [f"{label}{len(entries)} entries, {len(blocked)} blocked" + (f", {unknown_time} with unknown time" if unknown_time else "")]
    for entry in (blocked + [e for e in entries if e.get("status") != "blocked"])[:5]:
        lines.append(f"    {entry_line(entry, with_repo)}")
    return lines


def render(report: dict, window: str) -> str:
    out = [f"kitchen status · journal window {window}", *(f"setup      {p}" for p in report.get("setup", []))]
    for p in report["projects"]:
        out.append("")
        out.append(header(p))
        if "error" in p:
            continue
        for line in (nightly_line(p["nightly"]), gardener_line(p["gardener"]), audit_line(p["audit"]), prs_line(p["pull_requests"])):
            out.append(f"  {line}")
        count, *entries = journal_lines("journal    ", p["journal"])
        out += [f"  {count}", *entries]
        out.append(f"  {decisions_line(p['decisions'])}")
    out.append("")
    out += journal_lines("unattached  ", report["unattached"], with_repo=True)
    return "\n".join(out)


def exceptions(report: dict) -> list[str]:
    """Only the lines that are not green, each prefixed with its project; empty when everything is green."""
    out = [f"kitchen  setup      {p}" for p in report.get("setup", [])]
    for p in report["projects"]:
        if not header_green(p):
            out.append(header(p))
        if "error" in p:
            continue
        for line, exception in ((nightly_line(p["nightly"]), nightly_exception(p["nightly"])),
                                (gardener_line(p["gardener"]), gardener_exception(p["gardener"])),
                                (audit_line(p["audit"]), audit_exception(p["audit"])),
                                (prs_line(p["pull_requests"]), "error" in p["pull_requests"]),
                                (decisions_line(p["decisions"]), decisions_exception(p["decisions"]))):
            if exception:
                out.append(f"{p['name']}  {line}")
        out += [f"{p['name']}  journal    {entry_line(e)}" for e in p["journal"] if e.get("status") in journal.OWNER_STATUSES]
    out += [f"unattached  {entry_line(e, with_repo=True)}" for e in report["unattached"] if e.get("status") in journal.OWNER_STATUSES]
    return out
