"""`kitchen slot`: a per-user queue of N slots for heavy commands (builds, test suites), so parallel agents wait their turn.
Principles: `isolate`, `truthful-state`, `no-fallbacks`, `owner-attention`.
The CPU is shared state, so access to it is serialized by lock files no process can leave behind (`isolate`); every run
records how long it waited and ran, and an interrupted wait says it never ran (`truthful-state`); a bad slots.toml fails,
and there is no timeout and no "run anyway" (`no-fallbacks`); `--report` sizes N from records (`owner-attention`).

Slot i is `<state>/slots/<i>.lock`, held with a non-blocking flock on a descriptor of the holding process only: the
command never inherits it, so a daemon a build leaves behind cannot keep a slot, and a dead holder frees its slot by
dying. Callers wait in order of arrival: each holds a ticket in `<state>/slots/queue/`, and only the oldest live one tries
the slots. N is `slots = <n>` in ~/.config/kitchen/slots.toml, else max(1, cpus // 4).
A non-empty KITCHEN_SLOT means the caller already owns admission: the command runs directly and records nothing. A slot
sets it (the outer run holds the slot and records the time), and so does the automation's sealed verification, whose
job is admitted by its schedule and project lock and whose sandbox cannot write the queue.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import math
import os
import shlex
import signal
import sys
import time
import tomllib
from pathlib import Path

from .common import config_dir, git_common_dir, now, parse_ts, state_dir

POLL = 1.0
NOTICE_EVERY = 30.0
FORWARDED = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


class SlotError(Exception):
    """A setup problem (an unreadable slots.toml); `kitchen slot` exits 125 and runs nothing."""


class Interrupted(Exception):
    def __init__(self, signum: int):
        super().__init__(signal.Signals(signum).name)
        self.signum = signum


def config_path() -> Path:
    return config_dir() / "slots.toml"


def slots_dir() -> Path:
    return state_dir() / "slots"


def runs_path() -> Path:
    return slots_dir() / "runs.jsonl"


def cpus() -> int:
    return os.cpu_count() or 1


def slot_count() -> tuple[int, str]:
    """(N, where it came from). A file that exists but does not say a positive integer is an error, never a default."""
    path = config_path()
    if not path.exists():
        return max(1, cpus() // 4), f"default: {cpus()} cpus // 4; set slots = <n> in {path}"
    try:
        value = tomllib.loads(path.read_text(encoding="utf-8")).get("slots")
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise SlotError(f"cannot read {path}: {error}") from error
    if type(value) is not int or value < 1:
        raise SlotError(f"{path}: `slots` must be a positive integer, got {value!r}")
    return value, str(path)


def project_name(cwd: Path) -> str:
    """The folder of the repo's main checkout, so every worktree of one project counts as that project."""
    common = git_common_dir(cwd)
    if not common:
        return cwd.name
    common_path = Path(common)
    return common_path.parent.name if common_path.name == ".git" else common_path.name.removesuffix(".git")


def queue_dir() -> Path:
    return slots_dir() / "queue"


def lock_or_none(path: Path) -> int | None:
    """A read-only descriptor holding a shared flock on path, or None while someone holds it exclusively (or it is gone)."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        return None


def try_take(n: int) -> tuple[int, int] | None:
    """(index, fd) of the first free slot, or None. os.open's descriptor is close-on-exec: no command inherits it."""
    slots_dir().mkdir(parents=True, exist_ok=True)
    for index in range(n):
        fd = os.open(slots_dir() / f"{index}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return index, fd
        except BlockingIOError:
            os.close(fd)
    return None


def join_queue() -> tuple[Path, int]:
    """A ticket `<time_ns>-<pid>` in the queue, flocked by this process for as long as it waits (close-on-exec too).
    It is locked under a hidden name and then renamed, so no one ever sees it unlocked and takes it for a dead one's."""
    queue_dir().mkdir(parents=True, exist_ok=True)
    hidden = queue_dir() / f".new-{os.getpid()}-{time.time_ns()}"
    fd = os.open(hidden, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    ticket = queue_dir() / f"{time.time_ns():020d}-{os.getpid()}"
    os.rename(hidden, ticket)
    return ticket, fd


def leave_queue(ticket: Path, fd: int) -> None:
    with contextlib.suppress(FileNotFoundError):
        ticket.unlink()
    os.close(fd)


def place_in_queue(ticket: Path) -> int:
    """1 + the live tickets older than this one. A ticket nobody holds belongs to a dead waiter: it is removed."""
    ahead = 0
    for name in sorted(p.name for p in queue_dir().iterdir() if not p.name.startswith(".")):
        if name == ticket.name:
            return ahead + 1
        fd = lock_or_none(queue_dir() / name)
        if fd is None:
            ahead += 1  # held (or just gone; the next poll sees which)
            continue
        with contextlib.suppress(FileNotFoundError):
            (queue_dir() / name).unlink()
        os.close(fd)
    raise SlotError(f"this call's ticket {ticket} left the queue while it waited")


def write_holder(fd: int, command: str) -> None:
    os.ftruncate(fd, 0)
    os.pwrite(fd, json.dumps({"pid": os.getpid(), "command": command, "since": time.time()}).encode(), 0)


def release(fd: int) -> None:
    os.ftruncate(fd, 0)  # what a free slot's file says is ignored anyway; an empty file says nothing stale
    os.close(fd)  # closing the only descriptor drops the flock


def holders(n: int) -> list[dict | None]:
    """Per slot: None when free, else what its holder wrote (or {} while it has not written yet)."""
    found: list[dict | None] = []
    for index in range(n):
        path = slots_dir() / f"{index}.lock"
        fd = lock_or_none(path)
        if fd is not None or not path.exists():
            found.append(None)  # nobody holds it: whatever it says is ignored
        else:
            try:
                found.append(json.loads(path.read_bytes() or b"{}"))  # somebody holds it: its content is current
            except (OSError, json.JSONDecodeError):
                found.append({})
        if fd is not None:
            os.close(fd)
    return found


def describe(holder: dict | None) -> str:
    if holder is None:
        return "free"
    if not holder:
        return "held (its holder has not written who it is yet)"
    held_for = time.time() - holder.get("since", time.time())
    return f"held by pid {holder.get('pid', '?')} for {held_for:.0f} s: {holder.get('command', '?')}"


def waiting_line(n: int, waited: float, place: int) -> str:
    taken = "; ".join(f"{i}: {describe(h)}" for i, h in enumerate(holders(n)))
    return (f"kitchen slot: waiting {waited:.0f} s, number {place} in the queue, for one of {n} slot{'s' if n != 1 else ''}; "
            f"{taken}")


def wait_for_slot(n: int, command: str, out) -> tuple[int, int]:
    """Wait in order of arrival: only the oldest live ticket tries the slots. Poll about once a second, saying who holds
    the slots and this call's place at once and every NOTICE_EVERY seconds. No timeout."""
    start = time.monotonic()
    next_notice = start
    ticket, ticket_fd = join_queue()
    try:
        while True:
            place = place_in_queue(ticket)
            taken = try_take(n) if place == 1 else None
            if taken:
                write_holder(taken[1], command)
                return taken
            moment = time.monotonic()
            if moment >= next_notice:
                print(waiting_line(n, moment - start, place), file=out, flush=True)
                next_notice = moment + NOTICE_EVERY
            time.sleep(POLL)
    finally:
        leave_queue(ticket, ticket_fd)


class Run:
    """One run's record: written whatever happens, once the wait has started."""

    def __init__(self, command: str, project: str, n: int):
        self.record = {"ts": now().isoformat(timespec="seconds"), "command": command, "project": project, "slots": n,
                       "slot": None, "waited_s": None, "run_s": None, "exit": None}
        self.env: dict[str, str] = {}
        self.exit: int | None = None
        self.started = time.monotonic()
        self.running_since: float | None = None

    def write(self, interrupted: str | None = None) -> None:
        end = time.monotonic()
        self.record["waited_s"] = round((self.running_since or end) - self.started, 1)
        if self.running_since is not None:
            self.record["run_s"] = round(end - self.running_since, 1)
        self.record["exit"] = self.exit
        if interrupted:
            self.record["interrupted"] = interrupted
        slots_dir().mkdir(parents=True, exist_ok=True)
        with runs_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.record, ensure_ascii=False) + "\n")


@contextlib.contextmanager
def interruptible():
    """SIGINT, SIGTERM and SIGHUP raise Interrupted, so a wait or a body cut short still writes its records."""
    def interrupt(signum, _frame):
        raise Interrupted(signum)

    previous = {sig: signal.signal(sig, interrupt) for sig in FORWARDED}
    try:
        yield
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@contextlib.contextmanager
def held(command: str, project: str, out=sys.stderr):
    """Hold one slot for the body, which sees the slot's variables in `run.env` and sets `run.exit`. A forwarded signal
    raises Interrupted, during the wait and the body alike, and the record says so. Inside a slot: take none, record nothing."""
    run = Run(command, project, 0)
    if os.environ.get("KITCHEN_SLOT"):
        yield run
        return
    n, _ = slot_count()
    run.record["slots"] = n
    interrupted = None
    try:
        with interruptible():
            index, fd = wait_for_slot(n, command, out)
            try:
                run.record["slot"] = index
                run.env = {"KITCHEN_SLOT": str(index), "KITCHEN_SLOT_CPUS": str(max(1, cpus() // n))}
                run.running_since = time.monotonic()
                yield run
            finally:
                release(fd)
    except Interrupted as error:
        interrupted = str(error)
        raise
    finally:
        run.write(interrupted)


def spawn(command: list[str], env: dict, sigmask) -> int:
    """Start the command with this signal mask (the one from before the spawn blocked the forwarded signals)."""
    return os.posix_spawnp(command[0], command, env, setsigmask=sigmask)


def run_command(command: list[str], out=sys.stderr) -> int:
    """`kitchen slot -- <command>`: wait for a slot, run the command as a child holding none, pass its exit code on."""
    if os.environ.get("KITCHEN_SLOT"):
        try:
            os.execvp(command[0], command)  # nested: the outer run holds the slot and records it
        except OSError as error:
            print(f"kitchen slot: cannot run {command[0]}: {error}", file=out)
            return 127
    try:
        with held(shlex.join(command), project_name(Path.cwd()), out) as run:
            # A signal between the spawn and knowing the child would orphan it: block the forwarded signals until the
            # forwarding handlers know the pid, then let a pending one through to them.
            unblocked = signal.pthread_sigmask(signal.SIG_BLOCK, FORWARDED)
            try:
                try:
                    pid = spawn(command, {**os.environ, **run.env}, unblocked)
                except OSError as error:
                    print(f"kitchen slot: cannot run {command[0]}: {error}", file=out)
                    run.exit = 127
                    return 127

                def forward(signum, _frame):
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(pid, signum)

                for sig in FORWARDED:
                    signal.signal(sig, forward)  # held() puts the previous handlers back
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, unblocked)
            run.exit = os.waitstatus_to_exitcode(os.waitpid(pid, 0)[1])
            if run.exit < 0:
                run.exit = 128 - run.exit
            return run.exit
    except Interrupted as error:
        return 128 + error.signum


def show_state() -> str:
    n, source = slot_count()
    lines = [f"kitchen slot: {n} slot{'s' if n != 1 else ''} ({source})"]
    lines += [f"  {i}: {describe(h)}" for i, h in enumerate(holders(n))]
    return "\n".join(lines)


def percentile(values: list[float], p: int) -> float:
    """Nearest rank: the smallest value with at least p% of the values at or below it."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p / 100 * len(ordered)) - 1)]


def summary(label: str, runs: list[dict]) -> str:
    waited = [r["waited_s"] for r in runs if isinstance(r.get("waited_s"), (int, float))]
    ran = [r["run_s"] for r in runs if isinstance(r.get("run_s"), (int, float))]
    parts = [f"  {label:<20} {len(runs)} run{'s' if len(runs) != 1 else ''}"]
    if waited:
        parts.append(f"waited p50 {percentile(waited, 50):.0f} s, p95 {percentile(waited, 95):.0f} s, "
                     f"{sum(1 for w in waited if w > 60)} over 60 s")
    if ran:
        parts.append(f"ran p50 {percentile(ran, 50):.0f} s, p95 {percentile(ran, 95):.0f} s")
    never = len(runs) - len(ran)
    if never:
        parts.append(f"{never} never ran (interrupted while waiting)")
    return " · ".join(parts)


def report(since: datetime.datetime, label: str) -> str:
    path = runs_path()
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    runs, unreadable = [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            ts = parse_ts(entry["ts"])
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
            unreadable += 1
            continue
        if ts is None:
            unreadable += 1
        elif ts >= since:
            runs.append(entry)
    out = [f"kitchen slot: {len(runs)} run{'s' if len(runs) != 1 else ''} in the last {label} ({path})"]
    if runs:
        out.append(summary("all", runs))
        for project in sorted({r.get("project", "?") for r in runs}):
            out.append(summary(project, [r for r in runs if r.get("project", "?") == project]))
    if unreadable:
        out.append(f"  {unreadable} unreadable line{'s' if unreadable != 1 else ''} in {path}, not counted")
    return "\n".join(out)
