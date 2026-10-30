---
name: verify-kitchen-skills
description: "Prove a change to the kitchen (bin/kitchen, lib/, hooks/, skills/, automation/) before and after it lands: the check contract in a throwaway HOME, then the real installation read-only. Use when a task says verify, prove it works or no behavior change."
---

# verify-kitchen-skills

The kitchen is a CLI, its guards and its skills. It is proved in two places, never on the person's real `~/.claude`, `~/.codex` or `~/.config/kitchen` except read-only.

## The check contract

```bash
bin/check commit        # lint, private-data and secret scan, the fast suites (about 20 s)
bin/check integrate     # bin/kitchen check: every test, the automation suite included
bin/check --list        # the tiers
```

Every test runs in a throwaway repo and `HOME` (`KITCHEN_REPO`, `HOME`, `KITCHEN_CONFIG`, `KITCHEN_STATE`, `KITCHEN_DENYLIST`). A behavior change ships with a test that fails without it: run it red against the old code, then green.

## A disposable instance

```bash
T=$(mktemp -d)
HOME=$T KITCHEN_CONFIG=$T/config KITCHEN_STATE=$T/state bin/kitchen install < /dev/null
HOME=$T KITCHEN_CONFIG=$T/config KITCHEN_STATE=$T/state bin/kitchen doctor
```

## The real installation, read-only

```bash
kitchen doctor          # links, leftovers of older installs, agent CLIs, schedules
kitchen status --exceptions
```

## Feature map

`features/` holds one file per `kitchen` command: how it is reached and which test proves it. `tests/test_feature_map.py` fails when a command has no file there.

## Not covered

- Cloud sessions (Claude Code and Codex): never measured.
- Codex project hooks: load only in a trusted project after the person approves them in `/hooks`; the tests run the handlers by hand, not through Codex.
- The scheduled jobs on a real schedule: `bin/check` runs their tests, not a night of launchd or systemd.
- `automation/bin/run-job` on a real launchd: its tests fake `launchctl`; on systemd its calls were measured on a throwaway unit.
