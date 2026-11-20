#!/usr/bin/env python3
"""Local notifications for nightly exceptions (red or incomplete). Silent on green.

Nothing leaves the machine: Notification Center via osascript on macOS, notify-send on Linux.
Exits 0 when shown, 1 when the notifier failed, 2 when none is available.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from kitchen.credentials import redact  # noqa: E402

SHOWN, FAILED, NO_NOTIFIER = 0, 1, 2
TIMEOUT_SECONDS = 10
# The text travels as arguments, never inside the script source, so no quote in it can change the script.
APPLESCRIPT = ["-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)", "-e", "end run"]


def command(title: str, message: str) -> list[str] | None:
    system = platform.system()
    if system == "Darwin":
        tool = shutil.which("osascript")
        return [tool, *APPLESCRIPT, title, message] if tool else None
    if system == "Linux":
        tool = shutil.which("notify-send")
        return [tool, title, message] if tool else None
    return None


def notify(title: str, message: str) -> tuple[int, str]:
    title, message = redact(title), redact(message)
    argv = command(title, message)
    if argv is None:
        return NO_NOTIFIER, f"no notifier available on {platform.system()} (osascript on macOS, notify-send on Linux); not shown: {title}: {message}"
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return FAILED, f"{argv[0]} timed out after {TIMEOUT_SECONDS}s; not shown: {title}"
    except OSError as error:
        return FAILED, f"{argv[0]} could not run ({error}); not shown: {title}"
    if result.returncode != 0:
        reason = (result.stderr.strip().splitlines() or [""])[0]
        return FAILED, f"{argv[0]} exited {result.returncode}: {reason}; not shown: {title}"
    return SHOWN, f"notified: {title}"


if __name__ == "__main__":
    code, text = notify(sys.argv[1], sys.argv[2])
    if code != SHOWN:
        print(f"kitchen notify: {text}", file=sys.stderr)
    sys.exit(code)
