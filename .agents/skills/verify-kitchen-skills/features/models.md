# models

- **Reached by:** `kitchen models [get|set|add|remove] reviewer --author …`
- **Observable result:** the reviewers per author family, one per line, each with its CLI call; an unset role fails with the command that sets it; a reviewer of the author's own family is refused, pi judged by its model
- **Proved by:** tests/test_commands.py (ModelsTests)
