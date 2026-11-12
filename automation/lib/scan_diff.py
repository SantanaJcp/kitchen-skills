#!/usr/bin/env python3
"""Refuses a diff whose added lines look like a credential or hold a private term. Reads `git diff -U0` on stdin.

  git diff -U0 --no-color <base> <tree-ish> | scan_diff.py [<git-dir> <base>]

A layer independent of the sandbox: whatever the agent managed to read, a secret must not reach a PR.
Checks on every added line:
- the kitchen's own credential patterns (lib/kitchen/credentials.py, the ones `kitchen retro` uses): `credential pattern`;
- the private terms of the denylist, except those the base tree in <git-dir> already holds (lib/project_terms.py: the
  project's own vocabulary is not a leak into it; without <git-dir> <base>, every term counts): `private term`;
- known token shapes, kept here so this check does not depend on the kitchen's list;
- long high-entropy runs (random-looking keys with no known prefix);
and, per file, the known token shapes on each run of consecutive added lines joined together with quotes,
`+`, whitespace and line continuations removed, so a token split across lines is still seen.
This catches accidental leaks. Encoded or deliberately disguised exfiltration (base64, hex, reversed
strings...) is out of its reach; only the boundary stops that (sandboxed reads and the environment
allowlist, see README).
Prints one line per finding (file, line, kind; never the value) and exits 1 when anything matched, 2 (saying why)
when it cannot tell which terms the base holds.
"""
import math
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitchen.credentials import redact  # noqa: E402
from kitchen.common import private_terms  # noqa: E402
from project_terms import Unknown, args_terms  # noqa: E402

KNOWN = [
    ("GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("Anthropic or OpenAI key", r"\bsk-[A-Za-z0-9_-]{16,}"),
    ("AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("Slack token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    ("Google API key", r"\bAIza[0-9A-Za-z_-]{35}"),
    ("Stripe key", r"\b[rs]k_live_[0-9A-Za-z]{16,}"),
    ("npm token", r"\bnpm_[A-Za-z0-9]{36}"),
    ("JWT", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("private key", r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"),
    ("bearer token", r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}"),
]
KNOWN = [(label, re.compile(pattern)) for label, pattern in KNOWN]
CANDIDATE = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
MIN_ENTROPY = 4.0  # bits per character; hex (git SHAs, hashes) stays below it
MIN_SWITCHES = 0.45  # share of neighbouring characters that change class (lower, upper, digit, other):
# random keys switch about 6 times in 10 (98% of random 32-40 char keys pass 0.45), camelCase identifiers and
# paths only at word boundaries (contrived ones measured 0.32-0.38)


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def char_class(char: str) -> str:
    return "l" if char.islower() else "u" if char.isupper() else "d" if char.isdigit() else "o"


def switches(text: str) -> float:
    return sum(char_class(a) != char_class(b) for a, b in zip(text, text[1:])) / (len(text) - 1)


def high_entropy(line: str) -> bool:
    for run in CANDIDATE.findall(line):
        mixed = re.search(r"[a-z]", run) and re.search(r"[A-Z]", run) and re.search(r"[0-9]", run)
        if mixed and entropy(run) >= MIN_ENTROPY and switches(run) >= MIN_SWITCHES:
            return True
    return False


JOIN_STRIP = re.compile(r"""[\s'"`+\\]""")


def findings(diff: str, terms: list | None = None) -> list[str]:
    """<terms>: the private-term patterns to flag; None, every denylist term."""
    terms = (private_terms() or []) if terms is None else terms
    found, path, line_no = [], "?", 0
    run, run_start, run_flagged = [], 0, False

    def close_run():
        nonlocal run, run_flagged
        if len(run) > 1 and not run_flagged:
            joined = JOIN_STRIP.sub("", "".join(run))
            kinds = [label for label, pattern in KNOWN if pattern.search(joined)]
            if kinds:
                found.append(f"{path}:{run_start}-{run_start + len(run) - 1}: {', '.join(kinds)} (across lines)")
        run, run_flagged = [], False

    for line in diff.splitlines():
        if line.startswith("+++ "):
            close_run()
            path = line[6:] if line.startswith("+++ b/") else line[4:]
        elif line.startswith("@@"):
            close_run()
            match = re.search(r"\+(\d+)", line)
            line_no = int(match.group(1)) if match else 0
        elif line.startswith("+"):
            added = line[1:]
            kinds = [label for label, pattern in KNOWN if pattern.search(added)]
            if redact(added) != added:
                kinds.append("credential pattern")
            if any(term.search(added) for term in terms):
                kinds.append("private term")
            if not kinds and high_entropy(added):
                kinds.append("high-entropy string")
            if kinds:
                found.append(f"{path}:{line_no}: {', '.join(dict.fromkeys(kinds))}")
            if not run:
                run_start = line_no
            run.append(added)
            run_flagged = run_flagged or bool(kinds)
            line_no += 1
        else:
            close_run()
    close_run()
    return found


def main() -> int:
    try:
        terms = args_terms(sys.argv[1:])
    except Unknown as reason:
        print(reason)
        return 2
    found = findings(sys.stdin.read(), terms)
    for finding in found:
        print(finding)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
