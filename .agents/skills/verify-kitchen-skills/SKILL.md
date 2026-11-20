---
name: verify-kitchen-skills
description: "Prove a change to the kitchen (bin/kitchen, lib/, hooks/, skills/, automation/) before and after it lands: the check contract in a throwaway HOME, then the real installation read-only. Use when a task says verify, prove it works or no behavior change."
---

# verify-kitchen-skills

The kitchen is a CLI, its guard hooks, its skills, and the minimal nightly guard. Prove changes in throwaway homes first, then inspect the real installation read-only.

## The check contract

```bash
bin/check commit        # lint, private-data and secret scan, full test suite (seconds; no PrivateTmp needed)
bin/check integrate     # same full check
bin/check --list        # the tiers
```

Every test runs in a throwaway repo and `HOME` (`KITCHEN_REPO`, `HOME`, `KITCHEN_CONFIG`, `KITCHEN_STATE`, `KITCHEN_DENYLIST`). A behavior change ships with a test that fails without it.

## A disposable instance

```bash
T=$(mktemp -d)
HOME=$T KITCHEN_CONFIG=$T/config KITCHEN_STATE=$T/state bin/kitchen install < /dev/null
HOME=$T KITCHEN_CONFIG=$T/config KITCHEN_STATE=$T/state bin/kitchen doctor
```

## The real installation, read-only

```bash
kitchen doctor          # links, python3, gh, nightly timers
```

## Feature map

`features/` holds one file per `kitchen` command: how it is reached and which test proves it. `tests/test_feature_map.py` fails when a command has no file there or an orphaned file remains.

## Not covered

- Cloud sessions (Claude Code and Codex): never measured.
- Codex project hooks: load only in a trusted project after approval in `/hooks`; tests run handlers directly.
- Nightly timers on a real overnight launchd or systemd run.
