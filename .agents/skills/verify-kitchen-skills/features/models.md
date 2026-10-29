# models

- **Reached by:** `kitchen models [get|set|add|remove] reviewer --author …`, `kitchen models set worker …`, `kitchen models get worker --job … [--urgent] [--index N] [--json|--t3|--command]`
- **Observable result:** the reviewers per author family, one per line, each with its CLI call; an unset reviewer fails with the command that sets it; a reviewer of the author's own family is refused, pi judged by its model; a pi call on an id pi does not list exactly is refused. The worker's effort moved by the job between low and max, fast only with `--urgent`, accounts alternated by index, its `delegate_task` target per provider; an unset worker prints an empty target and says on stderr it runs on the session model
- **Proved by:** tests/test_commands.py (ModelsTests)
