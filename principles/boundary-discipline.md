# Boundary Discipline

- **Parent:** `design` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** wiring validation, error handling or framework adapters.

Validate, narrow types and handle errors where data enters the system: CLI arguments, config files, external APIs, network protocols. Inside, trust the types and do not re-validate. Business logic lives in pure functions; the shell around it is thin and mechanical.

- Parse raw data into domain types at the boundary; validate config when it is parsed, not deep in the logic.
- Expose domain concepts across the boundary, not its private representation: do not re-export transport, storage or framework types.
- Keep general mechanism inside and special-case policy at the edge.
- Ask: is this data crossing a boundary right now? If not, the check is redundant. Can this be a pure function the shell just calls? If yes, extract it.
