# status

- **Reached by:** `kitchen status [--exceptions]`
- **Observable result:** per project: branch, PRs, nightly, gardener, daily audit (each marked `triggered` when installed without its timer), decisions; what older installs left, as an exception; unknown, never zero
- **Proved by:** tests/test_commands.py (LogAndStatusTests)
