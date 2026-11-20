#!/usr/bin/env python3
"""Nightly guard history: one JSON line per run, rewritten in place by run_id.

  record.py <file> write <status> <failed_step> <cleanup> <sha> <run_id> <log> <started> <steps> [warning...]
  record.py <file> close-stale <current-run-id>
  record.py <file> mark-survivors <run-id> <pids>
"""
import datetime
import json
import os
import sys

KILLED = "killed before it could finish its record"


def load(path: str) -> list:
    if not os.path.exists(path):
        return []
    entries = []
    for line in open(path, encoding="utf-8").read().splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append(line)  # never drop what we cannot read
    return entries


def save(path: str, entries: list) -> None:
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write((entry if isinstance(entry, str) else json.dumps(entry)) + "\n")
    os.replace(tmp, path)


def parse_steps(text: str) -> list:
    steps = []
    for line in text.splitlines():
        seconds, status, name = line.split(" ", 2)
        if not seconds.isdigit() or status not in ("green", "red") or not name:
            raise ValueError(f"not a step line: {line!r}")
        steps.append({"name": name, "seconds": int(seconds), "status": status})
    return steps


def main() -> int:
    path, action, *args = sys.argv[1:]
    entries = load(path)
    if action == "close-stale":
        (current,) = args
        for entry in entries:
            if isinstance(entry, dict) and entry.get("status") == "running" and entry.get("run_id") != current:
                entry.update(status="incomplete", failed_step=entry.get("failed_step") or "killed",
                             warnings=[*entry.get("warnings", []), KILLED])
        save(path, entries)
        return 0
    if action == "mark-survivors":
        run_id, pids = args
        for entry in entries:
            if isinstance(entry, dict) and entry.get("run_id") == run_id:
                entry.update(status="incomplete", failed_step=f"survivors {pids}",
                             warnings=[*entry.get("warnings", []),
                                       f"survivors {pids} still held files in the clone after TERM and KILL"])
        save(path, entries)
        return 0
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    status, failed, cleanup, sha, run_id, log, started, steps, *warnings = args
    entry = {"ts": now, "started": started, "sha": sha or None, "status": status, "failed_step": failed or None,
             "cleanup": cleanup, "steps": parse_steps(steps), "warnings": warnings, "run_id": run_id, "log": log}
    for index, existing in enumerate(entries):
        if isinstance(existing, dict) and existing.get("run_id") == run_id:
            entries[index] = entry
            break
    else:
        entries.append(entry)
    save(path, entries)
    return 0


if __name__ == "__main__":
    sys.exit(main())
