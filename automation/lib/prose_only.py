#!/usr/bin/env python3
"""Refuses a diff that changes more than Markdown prose: what a scheduled audit may publish without a live drive.

  prose_only.py <git-dir> <base> <tree-ish>

Every changed file must be Markdown (`.md`), and no changed line, removed or added, may sit inside a fenced code
block (``` or ~~~, the fence lines included) of the old or the new file. Drive blocks, scripts and the verifier's
code change what runs; such a fix needs a live re-drive before it ships, which a sealed pass cannot do (see the
audit-verification skill and bin/daily-audit). Indented code blocks count as prose.
Prints one line per finding (file and line) and exits 1 when anything matched.
"""
import re
import subprocess
import sys

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def git(git_dir: str, *args: str) -> bytes:
    return subprocess.run(["git", f"--git-dir={git_dir}", *args], check=True, capture_output=True).stdout


def code_lines(text: str) -> set[int]:
    """1-based numbers of the lines inside fenced code blocks, fences included (CommonMark: a fence closes with the
    same character, at least as long, and nothing after it; an unclosed fence runs to the end)."""
    inside, opener, numbers = False, "", set()
    for number, line in enumerate(text.splitlines(), start=1):
        match = FENCE.match(line)
        if not inside and match:
            inside, opener = True, match.group(1)
            numbers.add(number)
        elif inside:
            numbers.add(number)
            if match and match.group(1)[0] == opener[0] and len(match.group(1)) >= len(opener) and not line.strip()[len(match.group(1)):].strip():
                inside = False
    return numbers


def blob(git_dir: str, rev: str, path: str) -> str:
    return git(git_dir, "cat-file", "blob", f"{rev}:{path}").decode("utf-8", errors="replace")


def main() -> int:
    git_dir, base, tree = sys.argv[1:]
    listing = git(git_dir, "diff", "--no-renames", "--name-status", "-z", base, tree).decode("utf-8").split("\0")
    findings = []
    for status, path in zip(listing[0::2], listing[1::2]):
        if not path.lower().endswith(".md"):
            findings.append(f"{path}: not Markdown")
            continue
        old = code_lines(blob(git_dir, base, path)) if status != "A" else set()
        new = code_lines(blob(git_dir, tree, path)) if status != "D" else set()
        diff = git(git_dir, "diff", "-U0", "--no-color", base, tree, "--", path).decode("utf-8", errors="replace")
        for line in diff.splitlines():
            hunk = HUNK.match(line)
            if not hunk:
                continue
            start_old, count_old = int(hunk.group(1)), int(hunk.group(2) or 1)
            start_new, count_new = int(hunk.group(3)), int(hunk.group(4) or 1)
            hit = [n for n in range(start_old, start_old + count_old) if n in old]
            hit += [n for n in range(start_new, start_new + count_new) if n in new]
            if hit:
                findings.append(f"{path}:{min(hit)}: inside a fenced code block")
    for finding in findings:
        print(finding)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
