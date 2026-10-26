# review-round

- **Reached by:** `kitchen review-round [--repo DIR] [--branch NAME] [--head REV] [--owner-approved TEXT | --show]`
- **Observable result:** round N of M per branch, shared by a repo's worktrees and clones of one origin; a run on the head of any earlier round is a retry that counts nothing; a third head is refused with BLOCKED on stderr and exit 1, and changes nothing, until the owner grants one more round; two runs at once admit one round; a damaged record is refused; a detached HEAD without `--branch` and an empty grant are refused
- **Proved by:** tests/test_commands.py (ReviewRoundTests)
