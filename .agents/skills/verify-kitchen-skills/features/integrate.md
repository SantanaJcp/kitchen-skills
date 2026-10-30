# integrate

- **Reached by:** `kitchen integrate <refs> --base …`
- **Observable result:** PASS or FAIL bound to the exact SHA vector, recorded with a digest of the checks
- **Clone refs:** a base `origin/<branch>` is also `refs/remotes/origin/<branch>` at the base SHA, so checks that compare against `origin/<base>` see the base the batch merges onto; a SHA or local-branch base creates no `origin/` ref
- **Proved by:** tests/test_commands.py (IntegrateTests)
