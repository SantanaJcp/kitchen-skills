# check-cost

- **Reached by:** `kitchen check-cost [repo] [--ref REF] [--command CMD]`; step 2 of the init playbook's "Prove it"
- **Observable result:** PASS, FAIL or UNKNOWN as the first word of the last line, after timing the check in a disposable clone under the kitchen's state directory with HEAD unchanged and right after a commit; one progress line on stderr as each run starts and ends; the log under `check-cost/logs/` in the state directory
- **Proved by:** tests/test_commands.py (CheckCostTests)
