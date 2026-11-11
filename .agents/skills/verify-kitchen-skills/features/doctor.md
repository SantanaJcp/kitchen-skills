# doctor

- **Reached by:** `kitchen doctor`
- **Observable result:** FAIL/WARN lines for missing links and for what older installs left (generated rules, global guards, rules.txt); agent CLIs and schedules; a job installed `--triggered` as triggered, with its last run; on Linux, a WARN when the temp directory is RAM and `CLAUDE_CODE_TMPDIR` does not put the agents' scratch directories on disk
- **Proved by:** tests/test_kitchen.py (DoctorTests), tests/test_setup.py (LegacyHookTests, EnvironmentDoctorTests, MountinfoTests, AgentScratchTests)
