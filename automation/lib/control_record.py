#!/usr/bin/env python3
"""The project's control record, as the daily audit job reads it: strict, or not at all.

  control_record.py <exit-code> <stdout-file> <out-dir>

<stdout-file> is what the project's static record check (AUDIT_CONTROL_RECORD_CMD --changed-since <baseline>) printed
on stdout, one line per changed feature page, in the map's order:

  <status> <page> digest=<16 hex>[ sha=<40 hex> at=<ISO 8601>]

with status current, partial, stale, none or no-controls. A page the check does not list did not change what runs
(its drive blocks and controls), so it owes no control. Exit 0 means every listed page is current; exit 1 that one is
not. Its stderr (a summary) is not read.

Writes to <out-dir>:
  facts.md     for the agent: each listed page, its status, and whether the job files a finding for it
  findings.md  a `## Findings` section with one `### control-owed-<page>: ...` finding per listed page that is not
               current (empty when there is none); the job files them like the agent's (lib/audit_findings.py)
Exits 2, saying why, for any other exit code, a line that does not parse, a page listed twice, a current page
without the run it cites, or an exit code that contradicts the statuses: the pass is then blocked, never clean.
Principles: `truthful-state` (an unreadable record is blocked, never "no record"), `prove` (a controlled page is
cited with the run that held, and nothing else is exempted).
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

STATUSES = ("current", "partial", "stale", "none", "no-controls")
ISO = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})"
LINE = re.compile(rf"^({'|'.join(STATUSES)}) (\S+) digest=([0-9a-f]{{16}})(?: sha=([0-9a-f]{{40}}) at=({ISO}))?$")
KEY_LIMIT = 60


def key(page: str) -> str:
    """A stable kebab-case finding key per page, within the 60 characters a key may have."""
    slug = re.sub(r"[^a-z0-9]+", "-", page.lower().removesuffix(".md")).strip("-") or "page"
    full = f"control-owed-{slug}"
    if len(full) <= KEY_LIMIT:
        return full
    return f"{full[:KEY_LIMIT - 9].rstrip('-')}-{hashlib.sha256(page.encode()).hexdigest()[:8]}"


def read(code: str, stdout: str) -> list[tuple[str, str, str, str | None, str | None]]:
    if code not in ("0", "1"):
        raise ValueError(f"the record check exited {code} (2: the record or the ref is unreadable or invalid)")
    rows, seen = [], set()
    for number, line in enumerate(stdout.splitlines(), start=1):
        match = LINE.match(line)
        if not match:
            raise ValueError(f"line {number} of the record check's stdout does not parse: {line[:120]!r}")
        status, page, digest, sha, at = match.groups()
        if page in seen:
            raise ValueError(f"the record check lists {page} twice")
        if status == "current" and not sha:
            raise ValueError(f"{page} is current but names no control run (sha=, at=)")
        seen.add(page)
        rows.append((status, page, digest, sha, at))
    owed = any(status != "current" for status, *_ in rows)
    if owed != (code == "1"):
        raise ValueError(f"the record check exited {code}, but {'a' if owed else 'no'} listed page is not current")
    return rows


def main() -> int:
    code, stdout_file, out_dir = sys.argv[1:4]
    try:
        if code not in ("0", "1"):  # before reading: a check that did not run may have left no stdout at all
            raise ValueError(f"the record check exited {code} (2: the record or the ref is unreadable or invalid)")
        rows = read(code, Path(stdout_file).read_text(encoding="utf-8"))
    except (ValueError, OSError, UnicodeDecodeError) as reason:
        print(reason, file=sys.stderr)
        return 2
    out = Path(out_dir)
    facts, findings = [], []
    for status, page, digest, sha, at in rows:
        if status == "current":
            facts.append(f"- `{page}`: current, digest {digest}; controls held at {sha} ({at}). Cite it; no step-5 finding.")
            continue
        facts.append(f"- `{page}`: {status}, digest {digest}{f'; last held at {sha} ({at})' if sha else ''}. "
                     f"The job files its step-5 finding `{key(page)}`; do not report it yourself.")
        findings.append(f"### {key(page)}: {page} is owed a hand control\n\n"
                        f"What runs for `{page}` (its drive blocks or the controls that target it) changed since the "
                        f"baseline, and the control record committed at the audited SHA says `{status}` for digest "
                        f"`{digest}`: no control run held at what runs now. Run its controls by hand and record them.\n")
    (out / "facts.md").write_text("\n".join(facts) + "\n" if facts else
                                  "- no feature page changed what runs since the baseline: no step-5 finding is owed\n")
    (out / "findings.md").write_text("## Findings\n\n" + "\n".join(findings) if findings else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
