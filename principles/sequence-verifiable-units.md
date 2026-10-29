# Sequence Work into Verifiable Units

- **Parent:** `prove` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** multi-step work (sweeps, migrations, runs of similar edits) and how you stack commits and pull requests.

Order the work as small units, each ending in a state you can check, and do not start the next until the current one is green. A break caught at the unit that caused it is cheap to find; one caught after a batch is buried under work built on it.

- **Execution.** Each unit is a bracket: known-good state, one change, run the check, go on. Start from a clean, current base so every check measures against the real baseline. When a script does the edits, the per-unit check is nearly free; run it anyway.
- **Delivery.** Stack commits and pull requests in the order that proves the work: the failing test, then the fix on top; or a subtraction before the reshape, a baseline before the treatment, the scaffold before the feature. Each commit lands on its own, and the sequence reads as an argument a reviewer can replay red, then green.

See [prove-it-works](prove-it-works.md) for keeping each check real and [build-the-lever](build-the-lever.md) for making it cheap.
