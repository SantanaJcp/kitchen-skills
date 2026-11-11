"""The denylist terms a project's own tree does not already hold.

A private term the project's base tree carries (the name of a vendor its code integrates, say) is the project's own
vocabulary, not a leak into it: the job neither refuses a diff that adds it nor scrubs it from the text it sends to
the project (lib/scan_diff.py, lib/redact.py, lib/audit_findings.py). A term the tree does not hold stays private.
One `git grep -q -I -F -w` per term on the tree at <rev>, with -i for an all-lowercase term: the denylist's smart case
and whole words (lib/kitchen/common.py). Binary files do not count. A git grep that fails raises Unknown: a term is
exempt only when git said the tree holds it.
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


def foreign_terms(git_dir: str, rev: str) -> list[re.Pattern]:
    """Patterns for the denylist terms the tree at <rev> in <git_dir> does not hold; [] without a denylist."""
    foreign = []
    for term in denylist_terms() or []:
        case = [] if term != term.lower() else ["-i"]
        found = subprocess.run(["git", "--git-dir", git_dir, "grep", "-q", "-I", "-F", "-w", *case, "-e", term, rev, "--"],
                               capture_output=True, text=True)
        if found.returncode == 1:
            foreign.append(term_pattern(term))
        elif found.returncode != 0:
            reason = (found.stderr.strip().splitlines() or [f"exit {found.returncode}"])[0]
            raise Unknown(f"cannot tell which private terms {rev[:12]} holds: git grep failed ({reason})")
    return foreign


def args_terms(args: list[str]) -> list[re.Pattern] | None:
    """[<git-dir> <rev>] from a command line: the foreign terms of that tree, or None (every denylist term) without."""
    if not args:
        return None
    if len(args) != 2:
        raise Unknown("expected <git-dir> <rev>")
    return foreign_terms(*args)
