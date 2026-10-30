"""What `kitchen doctor` checks outside the links: agent CLIs, the automation's tools, the scheduled jobs and whether their notifications can reach a server.
A job installed `--triggered` (automation/bin/install-schedule) has no timer: whatever starts it with automation/bin/run-job,
a T3 scheduled task for one, holds its schedule. It shows as triggered with its last run, read from what the job left."""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

from .common import state_dir

RECORDS = {"daily-audit": "audit", "weekly-gardener": "gardener"}  # the job's record; the retro keeps only run logs


def on_path(name: str) -> str | None:
    return shutil.which(name, path=os.environ.get("PATH", ""))


def srt(repo: Path) -> Path:
    return repo / "automation" / "node_modules" / ".bin" / "srt"


def automation_tools_problem(repo: Path) -> str | None:
    """Why the automation tests (and the gardener's verification) cannot run here, in one actionable line."""
    if not os.access(srt(repo), os.X_OK):
        return "srt is not installed, and the automation needs it: npm ci --ignore-scripts --prefix automation"
    if not on_path("node"):
        return "node is not on PATH, and srt (the automation's sandbox) runs on it: install Node.js"
    return None


def tools(repo: Path, config_dir: Path) -> list[tuple[str, str]]:
    report = []
    for name, why in (("claude", "Codex sessions run second-opinion reviewers with it"),
                      ("codex", "Claude Code sessions run second-opinion reviewers with it")):
        if not on_path(name):
            report.append(("WARN", f"`{name}` is not on PATH: {why}"))
    projects = sorted((config_dir / "automation").glob("*.env")) if (config_dir / "automation").is_dir() else []
    if projects:
        problem = automation_tools_problem(repo)
        if problem:
            report.append(("WARN", f"{problem} (automation configured for: {', '.join(p.stem for p in projects)})"))
    return report


def run(*args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(list(args), capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None


def units_dir(h: Path) -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or h / ".config") / "systemd" / "user"


def job_of(args: list) -> tuple[str, str | None]:
    """The job and project a unit runs, from its program: /bin/bash <kitchen>/automation/bin/<job> [project]."""
    return (Path(str(args[1])).name if len(args) > 1 else ""), (str(args[2]) if len(args) > 2 else None)


def triggered_jobs(system: str, h: Path) -> list[tuple[str, str, str | None]]:
    """(label, job, project) of each kitchen job installed without a schedule of its own: a systemd service without its
    timer, or a launchd job without StartCalendarInterval. Read from the files install-schedule writes."""
    found = []
    if system == "Linux":
        for service in sorted(units_dir(h).glob("kitchen-*.service")):
            if service.with_suffix(".timer").exists():
                continue
            try:
                lines = service.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            start = next((line.split("=", 1)[1].split() for line in lines if line.startswith("ExecStart=")), [])
            found.append((service.name, *job_of(start)))
    elif system == "Darwin":
        for plist in sorted((h / "Library" / "LaunchAgents").glob("com.kitchen.*.plist")):
            try:
                job = plistlib.loads(plist.read_bytes())
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue  # launchd() reports it
            if "StartCalendarInterval" not in job and isinstance(job.get("Label"), str):
                found.append((job["Label"], *job_of(job.get("ProgramArguments") or [])))
    return found


def last_run(job: str, project: str | None) -> str:
    """A triggered job's last run, from what the job left: its record, or the retro's newest run log."""
    if job == "weekly-retro":
        logs = sorted((state_dir() / "retro" / "logs").glob("retro-*.log"))
        if not logs:
            return "no run logged"
        ends = [line.split(" retro end: ", 1)[1] for line in logs[-1].read_text(errors="replace").splitlines()
                if " retro end: " in line]
        return f"{logs[-1]}, {ends[-1] if ends else 'no end line: running, or killed'}"
    if job not in RECORDS or not project:
        return f"unknown: {job or 'its program'} is not a job run-job starts"
    path = state_dir() / RECORDS[job] / f"{project}.jsonl"
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.is_file() else []
    if not lines:
        return f"no run recorded in {path}"
    try:
        run = json.loads(lines[-1])
    except json.JSONDecodeError:
        return f"unknown: the last line of {path} is not JSON"
    return f"{run.get('status')} at {run.get('ts')} ({path})"


def triggered_line(label: str, job: str, project: str | None) -> tuple[str, str]:
    if job == "nightly-guard":
        return ("WARN", f"{label} has no timer, and the nightly guard runs only on its own: "
                        f"`automation/bin/install-schedule --guard {project}`")
    return ("INFO", f"job {label} triggered (no timer: automation/bin/run-job starts it); last run: {last_run(job, project)}")


def schedules(system: str, h: Path) -> list[tuple[str, str]]:
    if system == "Darwin":
        return launchd(h)
    if system == "Linux":
        return systemd(h)
    return [("INFO", f"schedules not checked: kitchen schedules jobs only on macOS and Linux, this is {system}")]


def launchd(h: Path) -> list[tuple[str, str]]:
    plists = sorted((h / "Library" / "LaunchAgents").glob("com.kitchen.*.plist"))
    if not plists:
        return []
    listing = run("launchctl", "list")
    if listing is None or listing.returncode != 0:
        return [("WARN", f"cannot read `launchctl list`, so {len(plists)} kitchen schedules were not checked")]
    loaded = {line.split("\t")[-1].strip() for line in listing.stdout.splitlines()[1:] if line.strip()}
    triggered = {label: (job, project) for label, job, project in triggered_jobs("Darwin", h)}
    report = []
    for plist in plists:
        try:
            label = plistlib.loads(plist.read_bytes()).get("Label")
        except (OSError, plistlib.InvalidFileException, ValueError) as error:
            report.append(("WARN", f"cannot read {plist}: {error}"))
            continue
        if not isinstance(label, str) or not label:
            report.append(("WARN", f"{plist} has no Label, so launchd cannot load it"))
            continue
        if label in loaded and label in triggered:
            report.append(triggered_line(label, *triggered[label]))
        elif label in loaded:
            report.append(("INFO", f"schedule {label} loaded"))
        else:
            report.append(("WARN", f"{plist} is present but not loaded by launchctl: load it with "
                                   f"`launchctl bootstrap gui/$(id -u) {plist}`, or move it to the Trash if the job runs elsewhere"))
    return report


def systemd(h: Path) -> list[tuple[str, str]]:
    timers = sorted(units_dir(h).glob("kitchen-*.timer"))
    triggered = triggered_jobs("Linux", h)
    if not timers and not triggered:
        return []
    report = [triggered_line(*job) for job in triggered]
    for timer in timers:
        active = run("systemctl", "--user", "is-active", timer.name)
        if active is None:
            return [("WARN", f"cannot run `systemctl --user`, so {len(timers)} kitchen timers were not checked")]
        state = active.stdout.strip() or "unknown"
        if state == "active":
            report.append(("INFO", f"timer {timer.name} active"))
        else:
            report.append(("WARN", f"{timer} is present but {state}: start it with "
                                   f"`systemctl --user enable --now {timer.name}`, or move it to the Trash if the job runs elsewhere"))
    return report + [notification_server()]


# Asked from a transient unit, so it reaches the session bus the timers get, not this shell's. Shows nothing on screen.
NOTIFICATION_PROBE = ["systemd-run", "--user", "--wait", "--pipe", "--quiet", "busctl", "--user", "call",
                      "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "GetServerInformation"]


def notification_server() -> tuple[str, str]:
    """Whether notify-send from a kitchen timer can reach a notification server: the timers run as this user, so their
    notifications go to this user's session bus, and a desktop logged in as another user never receives them."""
    probe = run(*NOTIFICATION_PROBE)
    if probe is not None and probe.returncode == 0:
        name = probe.stdout.split('"')[1] if probe.stdout.count('"') >= 2 else probe.stdout.strip()
        return ("INFO", f"notification server {name} answers on the kitchen timers' session bus")
    if probe is None:
        return ("WARN", "notification server not checked: could not run systemd-run to ask the kitchen timers' session bus")
    reason = (probe.stderr.strip().splitlines() or [f"exit {probe.returncode}"])[0]
    return ("WARN", f"no notification server confirmed on the kitchen timers' session bus: asking it from a transient unit failed "
                    f"({reason}); without one, exception runs record `not notified` (automation/README.md, Notifications)")
