#!/usr/bin/env python3
"""Copies stdin to stdout with secrets and private denylist terms replaced."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from kitchen.transcripts import scrub  # noqa: E402

sys.stdout.write(scrub(sys.stdin.read()))
