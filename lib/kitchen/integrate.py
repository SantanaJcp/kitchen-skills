"""`kitchen integrate`: prove that branches work together, not just one by one.
Principles: `isolate` (a disposable clone, exact SHAs), `prove`, `truthful-state` (a recorded PASS binds to SHAs and checks).

Given a repo, a base and an ordered list of branches (or `pr:<n>` heads), it merges them in order onto the base
in a disposable clone and runs the project's check commands after every merge. The verdict is bound to the exact
SHA vector (base first, then each branch) and to a digest of everything that decides what runs (the check commands,
`path`, the base and every other key of the project's entry), so a PASS says nothing once any of those changes.

Check commands and the base live outside the repo, in ~/.config/kitchen/integrate.toml:
  [projects.kitchen-skills]
  base = "origin/main"
  checks = ["bin/kitchen check"]
There is no default base: without `base` there or --base, integrate fails.
Checks that compare against `origin/<base>` see the base the batch merges onto: when the base is the remote-tracking
ref origin/<branch>, the clone gets refs/remotes/origin/<branch> at the base SHA, copied from the source, never fetched.
A base given as a SHA or a local branch creates no origin/ ref.
Nothing it runs inherits GIT_* (or KITCHEN_REPO): a caller inside a git hook must not leak into the clone.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

from .common import config_dir, now, state_dir

FIXED_DATE = "2000-01-01T00:00:00+00:00"  # merge commits are deterministic for a given SHA vector
NOT_EXECUTION = ("gardener", "nightly")  # keys of a project's entry that only `kitchen status` reads


class IntegrateError(Exception):
    """A setup problem: bad ref, missing config. Reported as FAIL, never skipped."""


def clean_env(extra_path: list[str] | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k != "KITCHEN_REPO"}
    if extra_path:
        env["PATH"] = os.pathsep.join([os.path.expanduser(p) for p in extra_path] + [env.get("PATH", "")])
    return env


def git(cwd: Path, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, env=env or clean_env())


def config_path() -> Path:
    return config_dir() / "integrate.toml"


def project_config(project: str) -> dict:
    path = config_path()
    if not path.is_file():
        raise IntegrateError(f"no check commands: {path} does not exist (add [projects.{project}] checks = [...])")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise IntegrateError(f"cannot parse {path}: {error}") from error
    entry = data.get("projects", {}).get(project)
    if not entry or not entry.get("checks"):
        raise IntegrateError(f"no check commands for {project!r} in {path}")
    return entry


def configured_base(project: str) -> str | None:
    path = config_path()
    if not path.is_file():
        return None
    return tomllib.loads(path.read_text(encoding="utf-8")).get("projects", {}).get(project, {}).get("base")


def base_for(project: str, override: str | None) -> str:
    """The ref to merge onto: --base, else the project's `base`. Never a guessed default."""
    base = override or configured_base(project)
    if not base:
        raise IntegrateError(f"no base for {project!r}: pass --base or set base = \"<ref>\" under [projects.{project}] in {config_path()}")
    return base


def execution_digest(config: dict, base: str) -> str:
    """What decides what a run executes: the config it used (checks, path, any other key) and the base ref."""
    material = {key: value for key, value in config.items() if key not in NOT_EXECUTION} | {"base": base}
    return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def resolve(repo: Path, ref: str) -> str:
    result = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if result.returncode != 0:
        raise IntegrateError(f"cannot resolve {ref!r} in {repo}")
    return result.stdout.strip()


def remote_base_ref(repo: Path, base: str) -> str | None:
    """refs/remotes/origin/<branch> when the base names that remote-tracking ref; None for a SHA or a local branch."""
    full = git(repo, "rev-parse", "--symbolic-full-name", base).stdout.strip()
    return full if full.startswith("refs/remotes/origin/") else None


def resolve_pr(repo: Path, number: str, clone: Path) -> str:
    """Fetch refs/pull/<n>/head from the repo's origin into the disposable clone only."""
    url = git(repo, "remote", "get-url", "origin")
    if url.returncode != 0:
        raise IntegrateError(f"pr:{number} needs an origin remote in {repo}")
    fetched = git(clone, "fetch", "-q", url.stdout.strip(), f"+refs/pull/{number}/head:refs/integrate/pr-{number}")
    if fetched.returncode != 0:
        raise IntegrateError(f"cannot fetch pr:{number} from {url.stdout.strip()}: {fetched.stderr.strip()}")
    return resolve(clone, f"refs/integrate/pr-{number}")


def records_path(project: str) -> Path:
    return state_dir() / "integrate" / f"{project}.jsonl"


def run_checks(clone: Path, checks: list[str], env: dict, log) -> tuple[bool, str | None]:
    for command in checks:
        log.write(f"$ {command}\n")
        log.flush()
        result = subprocess.run(["sh", "-c", command], cwd=clone, env=env, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode != 0:
            return False, f"`{command}` exited {result.returncode}"
    return True, None


def integrate(repo: Path, base: str, refs: list[str], project: str, checks_override: list[str] | None = None) -> dict:
    repo = repo.resolve()
    env = clean_env()
    record = {"ts": now().isoformat(timespec="seconds"), "project": project, "repo": str(repo),
              "base": {"ref": base, "sha": None}, "branches": [], "steps": [], "status": "FAIL", "reason": None}
    run_id = now().strftime("%Y%m%dT%H%M%S%f")
    log_path = state_dir() / "integrate" / "logs" / f"{project}-{run_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    record["log"] = str(log_path)
    workdir = Path(tempfile.mkdtemp(prefix="kitchen-integrate-"))
    clone = workdir / "clone"
    try:
        with log_path.open("w", encoding="utf-8") as log:
            config = {"checks": checks_override} if checks_override else project_config(project)
            env = clean_env(config.get("path"))
            record["checks"] = config["checks"]
            record["execution_digest"] = execution_digest(config, base)
            record["base"]["sha"] = resolve(repo, base)
            # Every ref of the source (heads, remote-tracking, tags) so any resolvable SHA is present.
            subprocess.run(["git", "init", "-q", str(clone)], check=True, capture_output=True, env=env)
            fetched = git(clone, "fetch", "-q", "--no-tags", str(repo), "+refs/*:refs/source/*")
            if fetched.returncode != 0:
                raise IntegrateError(f"cannot copy {repo}: {fetched.stderr.strip()}")
            for ref in refs:
                match = re.fullmatch(r"pr:(\d+)", ref)
                sha = resolve_pr(repo, match.group(1), clone) if match else resolve(repo, ref)
                record["branches"].append({"ref": ref, "sha": sha})
            record["vector"] = [record["base"]["sha"]] + [b["sha"] for b in record["branches"]]

            checkout = git(clone, "checkout", "-q", "--detach", record["base"]["sha"])
            if checkout.returncode != 0:
                raise IntegrateError(f"cannot check out base: {checkout.stderr.strip()}")
            remote_ref = remote_base_ref(repo, base)
            if remote_ref:
                pinned = git(clone, "update-ref", remote_ref, record["base"]["sha"])
                if pinned.returncode != 0:
                    raise IntegrateError(f"cannot create {remote_ref} in the clone: {pinned.stderr.strip()}")
            merge_env = {**env, "GIT_AUTHOR_NAME": "kitchen integrate", "GIT_AUTHOR_EMAIL": "integrate@kitchen.invalid",
                         "GIT_COMMITTER_NAME": "kitchen integrate", "GIT_COMMITTER_EMAIL": "integrate@kitchen.invalid",
                         "GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE}
            for branch in record["branches"]:
                log.write(f"== merge {branch['ref']} {branch['sha']}\n")
                merged = git(clone, "merge", "-q", "--no-ff", "--no-edit", "-m", f"integrate {branch['ref']}", branch["sha"], env=merge_env)
                if merged.returncode != 0:
                    log.write(merged.stdout + merged.stderr)
                    record["steps"].append({"ref": branch["ref"], "merge": "conflict"})
                    record["reason"] = f"merge conflict at {branch['ref']}"
                    return record
                ok, why = run_checks(clone, config["checks"], env, log)
                record["steps"].append({"ref": branch["ref"], "merge": "ok", "check": "ok" if ok else why})
                if not ok:
                    record["reason"] = f"check failed after {branch['ref']}: {why}"
                    return record
            record["status"] = "PASS"
            return record
    except IntegrateError as error:
        record["reason"] = str(error)
        return record
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        records_path(project).parent.mkdir(parents=True, exist_ok=True)
        with records_path(project).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def recorded(repo: Path, base: str, refs: list[str], project: str, checks_override: list[str] | None = None) -> tuple[bool, str]:
    """Is there a PASS for the SHAs these refs point to right now, run with the checks configured right now?
    Any moved HEAD or changed check configuration invalidates it; a record without an execution digest proves nothing."""
    repo = repo.resolve()
    try:
        current = [resolve(repo, base)] + [resolve(repo, ref) for ref in refs]
        digest = execution_digest({"checks": checks_override} if checks_override else project_config(project), base)
    except IntegrateError as error:
        return False, str(error)
    history = records_path(project)
    runs = [json.loads(line) for line in history.read_text(encoding="utf-8").splitlines() if line.strip()] if history.is_file() else []
    same_refs = [r for r in runs if r.get("base", {}).get("ref") == base and [b["ref"] for b in r.get("branches", [])] == refs]
    same_vector = [r for r in same_refs if r.get("vector") == current]
    for run in reversed(same_vector):
        if run.get("execution_digest") == digest:
            return run["status"] == "PASS", f"{run['status']} at {run['ts']} on {vector_text(current)}"
    if same_vector:
        last = same_vector[-1]
        return False, f"stale: the check configuration changed (checks, path, base or another key) since the {last['status']} at {last['ts']} on {vector_text(current)}"
    if same_refs and same_refs[-1].get("vector"):
        last = same_refs[-1]
        moved = [f"{ref} {old[:8]}→{new[:8]}" for ref, old, new in zip([base] + refs, last["vector"], current) if old != new]
        return False, f"stale: {', '.join(moved)} since the {last['status']} at {last['ts']}"
    return False, "no integration record for these refs"


def vector_text(vector: list[str]) -> str:
    return "[" + " ".join(sha[:8] for sha in vector) + "]"


def render(record: dict) -> str:
    lines = [f"integrate {record['project']} · base {record['base']['ref']} {(record['base']['sha'] or '?')[:8]}"]
    for branch, step in zip(record["branches"], record["steps"] + [None] * len(record["branches"])):
        if step is None:
            lines.append(f"  {branch['ref']:<28} {branch['sha'][:8]}  not reached")
        else:
            lines.append(f"  {branch['ref']:<28} {branch['sha'][:8]}  merge {step['merge']}" + (f" · check {step['check']}" if "check" in step else ""))
    vector = vector_text(record["vector"]) if record.get("vector") else "[unresolved]"
    tail = "" if record["status"] == "PASS" else f": {record['reason']}"
    lines.append(f"{record['status']}  {record['project']} {vector}{tail} (log: {record['log']})")
    return "\n".join(lines)
