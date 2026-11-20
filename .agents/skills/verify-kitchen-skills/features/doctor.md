# doctor

- **Reached by:** `kitchen doctor`
- **Observable result:** FAIL/WARN/INFO lines for skill and CLI links, `core.hooksPath`, `python3`, `gh`, and installed nightly launchd/systemd timers
- **Proved by:** tests/test_kitchen.py (DoctorTests), tests/test_setup.py (EnvironmentDoctorTests)
