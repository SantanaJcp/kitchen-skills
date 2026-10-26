# models

- **Reached by:** `kitchen models [get|set|add|remove] reviewer --author …` and `kitchen models [get|set|remove] worker [--project DIR]`
- **Observable result:** the reviewers per author family, one per line, each with its CLI call; an unset role fails with the command that sets it; a reviewer of the author's own family is refused, pi judged by its model; a pi call on an id pi does not list exactly is refused; the worker is one entry, global or per project (the project is the repo's main worktree), `get` names the scope that answered, and `--author` with worker or `add worker` is refused
- **Proved by:** tests/test_commands.py (ModelsTests)
