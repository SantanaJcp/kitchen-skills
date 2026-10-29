# Model the Domain

- **Parent:** `design` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** writing stateful logic, or code that branches a lot or repeats a shape assumption across files.

Put the domain in a data structure instead of scattering it across conditionals. The right structure makes invalid states unrepresentable and deletes branches. Choosing it now is cheap; recovering it later reads as a refactor and gets deferred.

- A state machine instead of scattered booleans, phases or lifecycle checks.
- A typed model instead of loose parameters or repeated shape assumptions.
- A table, registry or discriminated union instead of branching spread across files.
- A reducer or command and event model instead of ad hoc mutation.
- A module organized around one body of domain knowledge, not around steps such as load, validate, save: execution order is not ownership.
- When nothing fits, write down what the code must never allow and how the data is read, and find the structure that encodes exactly that.

Do not force an abstraction: boring code is fine when the shape is clear, local and unlikely to grow.

You skipped this when a new feature grows an if/else chain by one branch, a second boolean must stay in sync with the first, or modules named after phases repeat the same domain rules.
