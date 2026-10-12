"""What `kitchen doctor` checks outside the links: agent CLIs, the automation's tools, the scheduled jobs and whether their notifications can reach a server."""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path


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
        if label in loaded:
            report.append(("INFO", f"schedule {label} loaded"))
        else:
            report.append(("WARN", f"{plist} is present but not loaded by launchctl: load it with "
                                   f"`launchctl bootstrap gui/$(id -u) {plist}`, or move it to the Trash if the job runs elsewhere"))
    return report


def systemd(h: Path) -> list[tuple[str, str]]:
    units = Path(os.environ.get("XDG_CONFIG_HOME") or h / ".config") / "systemd" / "user"
    timers = sorted(units.glob("kitchen-*.timer"))
    if not timers:
        return []
    report = []
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
    reason = "could not run systemd-run" if probe is None else (probe.stderr.strip().splitlines() or [f"exit {probe.returncode}"])[0]
    return ("WARN", f"the kitchen timers cannot show notifications: no notification server answers on this user's session bus "
                    f"({reason}); runs record `not notified` instead (automation/README.md, Notifications)")
