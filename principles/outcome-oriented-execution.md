# Outcome-Oriented Execution

- **Parent:** `no-fallbacks` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** planned rewrites and migrations with explicit phase boundaries.

Optimize for the verified end state, not for smooth intermediate states. Compatibility code written to keep every step stable becomes permanent debt.

- End-state integrity beats transitional stability. Breakage in between is fine when it is planned, scoped and reversible.
- Say up front where temporary breakage is acceptable.
- Keep strong checks on the areas you are touching while you migrate.
- Run the full static and runtime verification when the plan completes.

A temporary dual path is a fallback in waiting; converge instead.
