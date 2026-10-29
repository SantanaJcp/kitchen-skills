# guards

- **Reached by:** `kitchen guards [repo] [--check]`
- **Observable result:** `.kitchen/hooks/` copies run from the repo's `.claude/settings.json` and `.codex/hooks.json`; each tool's handler blocks a push to main in that repo; after a compaction in a session that loaded chef-mode, the `SessionStart` `compact` handler sends the agent back to its SKILL.md with the principles index; `--check` fails on drift
- **Proved by:** tests/test_guards.py (GuardsTests), tests/test_hooks.py (AfterCompactTests)
