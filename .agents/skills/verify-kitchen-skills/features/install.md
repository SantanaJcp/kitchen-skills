# install

- **Reached by:** `bin/kitchen install [--backup]`
- **Observable result:** skills (chef-mode among them) linked into `~/.claude/skills` and `~/.agents/skills`, `bin/kitchen` into `~/.local/bin/kitchen`, `core.hooksPath=.githooks`; no global rules, hooks, or agents
- **Proved by:** tests/test_kitchen.py (InstallTests)
