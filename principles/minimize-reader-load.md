# Minimize Reader Load

- **Parent:** `small-owned` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** reviewing or shaping code that is hard to trace.

Maintainability is the work a reader must do. Count two things: the layers between the question and the answer, and the hidden or mutable state the reader must hold. Line counts and "clean architecture" are proxies. A flat file of fifty globals can be as hard as six layers of adapters; guard both.

- Collapse layers that cost more than they save: one-caller wrappers, adapters with no second implementation, pass-through layers that repeat the same methods.
- Prefer interfaces that hide real decisions over broad ones that hide little.
- Shrink state scope: returns over mutations, locals over fields, fields over module state, module state over globals. Derive instead of syncing.
- Name an invariant once, at the boundary, not in every consumer.
- Before adding a layer or a piece of state, ask whether it removes at least as much load elsewhere.

The test: can a new reader answer "where does X come from?" and "what can change X?" in under 30 seconds?
