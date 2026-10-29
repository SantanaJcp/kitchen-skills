# Foundational Thinking

- **Parent:** `design` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** before writing logic: choosing core types and data structures, ordering scaffold and features, deciding what concurrent actors share.

Structural decisions protect option value; code-level decisions protect simplicity.

- **Data structures first.** Get the shape right before the logic: define the core types early, trace every access pattern, pick structures that match the main paths.
- **Scaffold first.** Whatever helps every later phase goes first: CI, lint, test infrastructure, shared types. Setup before features, tests before fixes. Subtraction comes before scaffolding.
- **Concurrency.** Before sharing state, ask "what if another actor changes this at the same time?" If the answer is not "nothing", isolate.
- At code level, deduplicate structure, not every line: three similar statements beat a premature abstraction. Prefer explicit over clever.
- Each increment lands a coherent abstraction or deepens one; do not spread a capability across callers as special cases.
