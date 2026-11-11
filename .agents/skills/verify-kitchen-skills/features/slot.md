# slot

- **Reached by:** `kitchen slot -- <command>`, `kitchen slot`, `kitchen slot --report [--since 7d]`; `kitchen integrate` and the init template's `bin/check` take a slot themselves
- **Observable result:** the command starts only in a free slot (one line on stderr while it waits), its exit code passes through, and `<state>/slots/runs.jsonl` gets one line with seconds waited and run
- **Proved by:** tests/test_slot.py
