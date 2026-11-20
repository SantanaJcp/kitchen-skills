# guards

- **Reached by:** `kitchen guards [repo] [--check]`
- **Observable result:** copies `kitchen-guard` and `chef-mode-after-compact` into `.kitchen/hooks/` (or references `hooks/` directly in this repo), writes one PreToolUse handler (with POSIX-sh prefilter) and one SessionStart compact handler to `.claude/settings.json` and `.codex/hooks.json`, removes legacy vendored files, and `--check` fails on drift
- **Proved by:** tests/test_guards.py (GuardsTests), tests/test_hooks.py
