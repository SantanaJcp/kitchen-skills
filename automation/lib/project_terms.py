"""The denylist terms a project's own tree does not already hold.

A private term the project's base tree carries (the name of a vendor its code integrates, say) is the project's own
vocabulary, not a leak into it: the job neither refuses a diff that adds it nor scrubs it from the text it sends to
the project (lib/scan_diff.py, lib/redact.py, lib/audit_findings.py). A term the tree does not hold stays private.
git grep (-I -F, -i for an all-lowercase term) lists the tree's lines that hold the term's text; a term is the
project's only when one of them matches the very pattern the scanner and the scrubber use (lib/kitchen/common.py: smart case, whole words by Python's Unicode-aware \\b). git's own -w
is byte-wise and would count `Zorblat` inside `Zorblaté`. Binary files do not count. A git grep that fails raises
Unknown: a term is exempt only when the tree was read and holds it.
Principles: `truthful-state` (an unknown answer exempts nothing), `no-fallbacks`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from kitchen.common import denylist_terms, term_pattern  # noqa: E402


class Unknown(Exception):
    pass


def holds(git_dir: str, rev: str, term: str) -> bool:
    """Whether a text file of the tree at <rev> holds <term> as the denylist pattern matches it. Stops at the first hit."""
    pattern = term_pattern(term)
    case = [] if term != term.lower() else ["-i"]
    grep = subprocess.Popen(["git", "--git-dir", git_dir, "grep", "-h", "-I", "-F", *case, "-e", term, rev, "--"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
    for line in grep.stdout:
        if pattern.search(line):
            grep.kill()
            grep.communicate()
            return True
    _, error = grep.communicate()
    if grep.returncode not in (0, 1):
        reason = (error.strip().splitlines() or [f"exit {grep.returncode}"])[0]
        raise Unknown(f"cannot tell which private terms {rev[:12]} holds: git grep failed ({reason})")
    return False


def foreign_terms(git_dir: str, rev: str) -> list[re.Pattern]:
    """Patterns for the denylist terms the tree at <rev> in <git_dir> does not hold; [] without a denylist."""
    return [term_pattern(term) for term in denylist_terms() or [] if not holds(git_dir, rev, term)]


def args_terms(args: list[str]) -> list[re.Pattern] | None:
    """[<git-dir> <rev>] from a command line: the foreign terms of that tree, or None (every denylist term) without."""
    if not args:
        return None
    if len(args) != 2:
        raise Unknown("expected <git-dir> <rev>")
    return foreign_terms(*args)
