#!/usr/bin/env python3
"""One GitHub issue per pending finding of the daily audit: what bin/daily-audit reads from the report and the open
issues, and what it sends.

  audit_findings.py open <issues.json>
      the open findings issues the agent sees before its pass, one per line: `- <key>: <title> (issue #N)`
  audit_findings.py plan <report> <issues.json> <limit> <out-dir> <header.md> <project> [<unpublished-reason>]
      one line per finding, `<action> <number-or--> <key> <dir>`: `create` (no open issue carries its key),
      `comment` (its open issue's last text differs) or `none` (its open issue already says the same). <dir> holds
      `title` and `body.md`, redacted and cut under GitHub's limits. With <unpublished-reason>, the prose fixes the
      pass committed did not ship, and that is one more finding, keyed `unpublished-prose-fixes`.

A finding is a `### <key>: <title>` heading inside the report's `## Findings` section, with its text up to the next
heading of level 3 or less (fenced code blocks are text). The key is kebab-case and the agent keeps it while the
finding is pending, reusing the one `open` listed. An issue carries its key, and the digest of the finding's
redacted text, in a hidden marker on its first line; each comment the job adds carries the new digest, and the
last one wins. Matching never searches titles, so a renamed issue still matches.
<issues.json> is `gh issue list --label <label> --state open --json number,title,body,comments --limit <limit>`;
a list that reaches <limit> may miss the matching issue, so the plan refuses it. A malformed or empty Findings
section, a duplicate key, or a key that redaction changed refuses the whole plan (exit 2, saying why): nothing is
filed from a report the job cannot read exactly.
Principles: `owner-attention` (one issue per finding, a comment only when it changed), `isolate` (a rerun opens
nothing new), `truthful-state` (a report the job cannot split is never filed in part).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from kitchen.transcripts import scrub  # noqa: E402  (redact.py's patterns)

KEY = r"[a-z0-9]+(?:-[a-z0-9]+)*"
SECTION = re.compile(r"^##\s+Findings\s*$")
HEADING = re.compile(r"^(#{1,3})\s")
FINDING = re.compile(rf"^###\s+({KEY}):\s+(\S.*?)\s*$")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
MARKER = re.compile(rf"<!-- kitchen-audit-finding key=({KEY}) digest=([0-9a-f]{{16}}) -->")
KEY_LIMIT, TITLE_LIMIT, BODY_LIMIT = 60, 256, 60000  # GitHub refuses a title over 256 and a body over 65536
UNPUBLISHED = "unpublished-prose-fixes"


class Refused(Exception):
    pass


def findings(report: str) -> list[tuple[str, str, str]]:
    """(key, title, text) for each finding of the report's Findings section, in order."""
    found: list[list[str]] = []
    section = seen = False
    fence = ""
    for line in report.splitlines():
        match = FENCE.match(line)
        if fence:
            if match and match.group(1)[0] == fence[0] and len(match.group(1)) >= len(fence) and not line.strip().strip(fence[0]):
                fence = ""
        elif match:
            fence = match.group(1)
        elif SECTION.match(line):
            if seen:
                raise Refused("the report has two `## Findings` sections")
            section = seen = True
            continue
        elif section and (heading := HEADING.match(line)):
            if len(heading.group(1)) < 3:
                section = False
                continue
            finding = FINDING.match(line)
            if not finding:
                raise Refused(f"a finding heading is not `### <kebab-key>: <title>`: {line[:80]}")
            found.append([finding.group(1), finding.group(2)])
            continue
        if section and found:
            found[-1].append(line)
    if not seen:
        raise Refused("the report has no `## Findings` section")
    if not found:
        raise Refused("the report's Findings section lists no `### <key>: <title>` finding")
    result, keys = [], set()
    for key, title, *lines in found:
        text = "\n".join(lines).strip()
        if len(key) > KEY_LIMIT:
            raise Refused(f"finding key {key[:KEY_LIMIT]}... is longer than {KEY_LIMIT} characters")
        if key in keys or key == UNPUBLISHED:
            raise Refused(f"finding key {key} is used twice")
        if not text:
            raise Refused(f"finding {key} has no text")
        keys.add(key)
        result.append((key, title, text))
    return result


def digest(title: str, text: str) -> str:
    normal = "\n".join(line.rstrip() for line in f"{title}\n{text}".strip().splitlines())
    return hashlib.sha256(normal.encode()).hexdigest()[:16]


def open_issues(path: str, limit: int | None = None) -> dict[str, tuple[int, str, str]]:
    """key -> (number, title, last digest) of the open issues that carry a key marker; the lowest number wins."""
    issues = json.loads(Path(path).read_text(encoding="utf-8") or "[]")
    if not isinstance(issues, list):
        raise Refused("the open issue list is not a JSON list")
    if limit is not None and len(issues) >= limit:
        raise Refused(f"{len(issues)} open findings issues reach the listing limit of {limit}; a match could be missed")
    by_key: dict[str, tuple[int, str, str]] = {}
    for issue in sorted(issues, key=lambda issue: issue["number"]):
        marker = MARKER.match(str(issue.get("body") or ""))
        if not marker or marker.group(1) in by_key:
            continue
        last = marker.group(2)
        for comment in issue.get("comments") or []:
            mark = MARKER.match(str(comment.get("body") or ""))
            if mark and mark.group(1) == marker.group(1):
                last = mark.group(2)
        by_key[marker.group(1)] = (int(issue["number"]), str(issue.get("title") or ""), last)
    return by_key


def cut(text: str, note: str) -> str:
    return text if len(text) <= BODY_LIMIT else text[: BODY_LIMIT - len(note)] + note


def plan(report_path, issues_path, limit, out_dir, header_path, project, unpublished=None) -> list[str]:
    report = Path(report_path).read_text(encoding="utf-8", errors="replace")
    # Redacted before parsing, so a key, a title and a text that leave the machine are all redacted.
    items = findings(scrub(report))
    raw_keys = [key for key, _, _ in findings(report)]
    if raw_keys != [key for key, _, _ in items]:
        raise Refused("redaction changed a finding key; nothing is filed")
    if unpublished is not None:
        items.append((UNPUBLISHED, "Prose fixes the pass committed did not ship",
                      f"The pass committed prose fixes on its branch, and the job did not publish them: {scrub(unpublished)}.\n\n"
                      "The fixes the report lists are still drift until a pass ships them."))
    known = open_issues(issues_path, limit)
    header = scrub(Path(header_path).read_text(encoding="utf-8"))
    lines = []
    for index, (key, title, text) in enumerate(items, start=1):
        mark = digest(title, text)
        number, _, last = known.get(key, (0, "", ""))
        action = "create" if not number else "none" if last == mark else "comment"
        folder = Path(out_dir) / str(index)
        folder.mkdir(parents=True)
        prefix, suffix = f"Audit finding in {project}: ", f" [{key}]"
        room = TITLE_LIMIT - len(prefix) - len(suffix)
        (folder / "title").write_text(prefix + (title if len(title) <= room else title[: room - 3] + "...") + suffix)
        lead = ("This finding is still pending, and its text changed:" if action == "comment" else
                "A pass by hand closes this issue once the finding is fixed; the daily audit never closes it, and "
                "comments only when the finding's text changes.")
        body = cut(f"{header}\n{lead}\n\n---\n\n### {title}\n\n{text}\n",
                   "\n\n[Truncated: the full report is on the audit machine, at the path above.]\n")
        (folder / "body.md").write_text(f"<!-- kitchen-audit-finding key={key} digest={mark} -->\n{body}")
        lines.append(f"{action} {number or '-'} {key} {folder}")
    return lines


def main() -> int:
    action, *args = sys.argv[1:]
    try:
        if action == "open":
            for key, (number, title, _) in open_issues(args[0]).items():
                print(f"- {key}: {title} (issue #{number})")
            return 0
        if action == "plan":
            print("\n".join(plan(args[0], args[1], int(args[2]), *args[3:])))
            return 0
    except Refused as reason:
        print(reason, file=sys.stderr)
        return 2
    print(f"unknown action {action}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
