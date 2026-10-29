# Make Operations Idempotent

- **Parent:** `isolate` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** designing commands, installers, lifecycle steps or loops that run amid crashes, restarts and retries.

An operation must converge to the right state however many times it runs and wherever the last run stopped. Derive each step from the current state, not from what the previous run meant to do: read the state, compare it with the goal, apply only the difference. A repeat then finds nothing left to do.

- Anything a run leaves behind (a lock, a temp file, a half-written record, a claimed job) must be recognizable as stale by the next run, by evidence that holds wherever the last run stopped. Write to a temp path and rename into place; compare artifacts by content, not by creation order.
- Ask: what if it runs twice in a row? What if the last run crashed at every possible point? Does a rerun reach the same end state?

If any answer is "it depends on what was left behind", add a reconciliation step. `install` and `kitchen guards` rerun byte-identical.
