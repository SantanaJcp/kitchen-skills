# Working on this repo

This file is for agents editing the kitchen itself. `PRINCIPLES.md` says what the kitchen does and why; read it before changing a control. Agents reach it through `/chef-mode`.

Autonomy: merge. It applies in chef mode (`/chef-mode`); outside it, open the pull request and leave the merge to the owner.

## Rules

- The repo is public. Never commit personal absolute paths, client or project names, credentials, or machine-specific config. Per-machine config lives in `~/.config/kitchen/`.
- `bin/kitchen` and `lib/`: Python 3.11+ standard library only. CLI helpers live in `lib/kitchen/`; `bin/kitchen` wires commands.
- `hooks/`: Python that also runs on the system `python3` (3.9+), stdlib only.
- `automation/`: bash, `lsof`, and Python 3.9+ stdlib. Add no external dependencies.
- A skill is a workflow, not a manual: `SKILL.md` stays under 150 lines (`bin/kitchen check` enforces it). Skill names and descriptions are English and kebab-case; folder name equals frontmatter `name`; descriptions are one line.
- Every control (a guard, a scheduled job, a CLI module that decides PASS/FAIL/unknown, a skill) names the principles it enforces in a `Principles:` line, and non-control helpers are listed in `NOT_CONTROLS` in `tests/test_principles.py`. A new principle goes in `PRINCIPLES.md` with an `Enforced by:` line.

## Verify

- `bin/check` is the check contract (`commit`, `integrate`, `nightly`, `verify-tree`; `bin/check --list`), and `verify-kitchen-skills` says how to prove a change. `bin/kitchen check` lints skills, scans for private data and secrets, and runs the full test suite in seconds. Never bypass `.githooks/pre-commit` with `--no-verify`.
- `hooks/kitchen-guard` has cases in `tests/corpus/hooks/cases.json`; evasions it cannot catch go in as `known-limit` cases.
- `bin/kitchen doctor` checks the real installation on this machine.
- Tests use a throwaway repo and `HOME` (`KITCHEN_REPO`, `HOME`, `KITCHEN_DENYLIST`, `KITCHEN_STATE`, `KITCHEN_CONFIG`); never let a test touch the real `~/.claude`, `~/.codex`, `~/.agents`, or `~/.config/kitchen`.
- A behavior change ships with a test that fails without it.
