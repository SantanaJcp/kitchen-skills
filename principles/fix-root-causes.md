# Fix Root Causes

- **Parent:** `root-cause` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** debugging anything broken, failing, flaky or slow.

Fix the fault, not the place it surfaced. The symptom is where a wrong state became visible. The root cause is the first wrong decision: the first place the system did something it should not, or failed to handle a state it should.

- Reproduce first, red.
- Ask "why" at each step until the answer is a wrong decision in the code, not a consequence of an earlier one.
- When stuck, instrument: read the actual error and the actual state. Do not guess.
- Ask whether the state that triggered the symptom is legitimate. A guard is a fix only when it is: then the code that broke on it is the cause. If the state should never exist, a check where it broke hides the bug; find what produced it. Guards, retries, swallowed errors, clamped values and dropped state are the usual hiding places.
- When it fails only after a restart, suspect persisted state first: config, caches, lock files, serialized state.
- Fix the pattern, not the instance: grep for every place with the same fault.
- A workaround that needs a paragraph of comment to justify it means the code is wrong.
- After a deep dig, propose the cheap, always-on signal (a log line, a status field, an assertion) that would have shown the cause at first look.

The `find-the-cause` skill is the workflow.
