# doctor

- **Reached by:** `kitchen doctor`
- **Observable result:** FAIL/WARN lines for missing links and for what older installs left (generated rules, global guards, rules.txt); agent CLIs and schedules; a job installed `--triggered` as triggered, with its last run
- **Proved by:** tests/test_kitchen.py (DoctorTests), tests/test_setup.py (LegacyHookTests, EnvironmentDoctorTests)
