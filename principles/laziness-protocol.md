# Laziness Protocol

- **Parent:** `small-owned` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** refactoring, sizing a diff, or tempted to add an abstraction, a layer or a flag threaded through calls.

Bias to deletion and to the smallest change that solves the problem.

- **Consolidate decisions.** Do not make the same choice in several places: put it behind one source of truth and pass the result on as a simple value.
- **Minimize the diff.** Fewer lines beat elegant boilerplate.
- **No speculative structure.** An abstraction, layer or option nobody asked for yet is cost now and maybe never value.

The kitchen replaced a large suite with a few hundred owned lines (PR #21).
