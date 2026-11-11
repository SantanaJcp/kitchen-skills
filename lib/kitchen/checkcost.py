"""`kitchen check-cost`: prove a project's check does not get more expensive just because HEAD moved.
Principles: `prove` (the check is run for real, timed, in a clone), `truthful-state` (a failed run or a drifting machine is UNKNOWN, never PASS), `isolate` (a disposable clone; the source repo is only read).

A build that writes the commit id into its outputs recompiles everything after any commit, even an empty one, while
the same build with nothing changed reuses its outputs. Agents commit several times per task, so that cost multiplies.

In a disposable clone at the resolved SHA, the check command runs up to four times, each timed:
  warm     as checked out; fills caches, not judged
  same     after a new untracked probe file; HEAD unchanged
  moved    after committing everything (HEAD moves), then a second new probe file
  confirm  only when `moved` looks too expensive: a third new probe file, HEAD unchanged
A new probe file before each judged run defeats a check that reuses a PASS for an identical tree: `same` and `moved`
both run for real and differ only in whether HEAD moved.

The clone lives under the kitchen's state directory, on disk, not in the system temp dir (on some machines /tmp is
RAM, and a build there eats memory). Nothing it runs inherits GIT_* or KITCHEN_REPO.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .common import now, state_dir
from .integrate import IntegrateError, clean_env, git, resolve

FACTOR = 2.0  # `moved` may cost up to this many times `same`
FLOOR = 5.0  # seconds: a smaller difference is noise, whatever the ratio
DEFAULT_COMMAND = "bin/check commit"
PROBE = ".kitchen-check-cost-{n}"
IDENTITY = {"GIT_AUTHOR_NAME": "kitchen check-cost", "GIT_AUTHOR_EMAIL": "check-cost@kitchen.invalid",
            "GIT_COMMITTER_NAME": "kitchen check-cost", "GIT_COMMITTER_EMAIL": "check-cost@kitchen.invalid"}


def too_slow(moved: float, baseline: float, factor: float = FACTOR, floor: float = FLOOR) -> bool:
    """`moved` exceeds both the factor and the floor against `baseline`."""
    return moved > factor * baseline and moved - baseline >= floor


def verdict(same: float, moved: float, confirm: float | None, factor: float = FACTOR, floor: float = FLOOR) -> tuple[str, str]:
    """(PASS|FAIL|UNKNOWN, message) from the measured seconds alone. `confirm` is None when it was not needed."""
    numbers = f"same HEAD {same:.1f} s, moved HEAD {moved:.1f} s" + (f", same HEAD again {confirm:.1f} s" if confirm is not None else "")
    if not too_slow(moved, same, factor, floor):
        return "PASS", f"the check costs about the same after a commit ({numbers})"
    if confirm is None:
        raise ValueError("`moved` is too slow against `same`: the verdict needs the `confirm` run")
    if too_slow(moved, confirm, factor, floor):
        ratio = moved / max(same, confirm, 1e-3)
        return "FAIL", (f"this check costs {ratio:.1f}x more right after a commit ({numbers}); "
                        "look for something that puts the commit id into the build's inputs")
    return "UNKNOWN", (f"moved HEAD was slow against the first run but not against the confirm run ({numbers}); "
                       "the machine's load changed between runs, so nothing is proved: run it again on a quieter machine")


def check_cost(repo: Path, ref: str = "HEAD", command: str = DEFAULT_COMMAND,
               factor: float = FACTOR, floor: float = FLOOR, progress=sys.stderr) -> dict:
    repo = repo.resolve()
    root = state_dir() / "check-cost"
    log_path = root / "logs" / f"{repo.name}-{now().strftime('%Y%m%dT%H%M%S%f')}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"repo": str(repo), "ref": ref, "sha": None, "command": command, "times": {},
              "status": "UNKNOWN", "message": None, "log": str(log_path)}
    env = clean_env()
    workdir = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    clone = workdir / "clone"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            record["sha"] = resolve(repo, ref)
            subprocess.run(["git", "init", "-q", str(clone)], check=True, capture_output=True, env=env)
            fetched = git(clone, "fetch", "-q", "--no-tags", str(repo), "+refs/*:refs/source/*")
            if fetched.returncode != 0:
                raise IntegrateError(f"cannot copy {repo}: {fetched.stderr.strip()}")
            checkout = git(clone, "checkout", "-q", "--detach", record["sha"])
            if checkout.returncode != 0:
                raise IntegrateError(f"cannot check out {record['sha']}: {checkout.stderr.strip()}")

            def run(name: str) -> float:
                print(f"check-cost: {name} started", file=progress, flush=True)
                log.write(f"== {name}: $ {command}\n")
                log.flush()
                start = time.monotonic()
                result = subprocess.run(["sh", "-c", command], cwd=clone, env=env, stdin=subprocess.DEVNULL,
                                        stdout=log, stderr=subprocess.STDOUT)
                seconds = time.monotonic() - start
                record["times"][name] = seconds
                print(f"check-cost: {name} done in {seconds:.1f} s (exit {result.returncode})", file=progress, flush=True)
                if result.returncode != 0:
                    raise IntegrateError(f"the {name} run of `{command}` exited {result.returncode}")
                return seconds

            def probe(n: int) -> None:
                (clone / PROBE.format(n=n)).write_text(f"{n} {time.time_ns()}\n", encoding="utf-8")

            run("warm")
            probe(1)
            same = run("same")
            committed = [git(clone, *args, env={**env, **IDENTITY}) for args in (
                ("add", "-A"),
                ("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "kitchen check-cost probe"))]
            for step in committed:
                if step.returncode != 0:
                    raise IntegrateError(f"cannot commit in the clone: {(step.stderr or step.stdout).strip()}")
            probe(2)
            moved = run("moved")
            confirm = None
            if too_slow(moved, same, factor, floor):
                probe(3)
                confirm = run("confirm")
            record["status"], record["message"] = verdict(same, moved, confirm, factor, floor)
            return record
    except IntegrateError as error:
        record["message"] = str(error)
        return record
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        if workdir.exists():
            print(f"check-cost: could not remove the work directory {workdir}; remove it by hand", file=progress, flush=True)


def render(record: dict) -> str:
    sha = (record["sha"] or "?")[:8]
    return f"{record['status']}  {os.path.basename(record['repo'])} {sha}: {record['message']} (log: {record['log']})"
