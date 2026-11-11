#!/usr/bin/env python3
"""Copies stdin to stdout with secrets replaced, using the same patterns as `kitchen retro`.

  redact.py [<git-dir> <rev>]

With <git-dir> <rev>, the private terms the tree at <rev> already holds are the project's own vocabulary and stay
(lib/project_terms.py); without, every denylist term is replaced. Exits 2, writing nothing, when it cannot tell.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from kitchen.transcripts import scrub  # noqa: E402
from project_terms import Unknown, args_terms  # noqa: E402

try:
    terms = args_terms(sys.argv[1:])
except Unknown as reason:
    print(f"redact: {reason}", file=sys.stderr)
    sys.exit(2)
sys.stdout.write(scrub(sys.stdin.read(), terms))
