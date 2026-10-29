# Migrate Callers Then Delete Legacy APIs

- **Parent:** `no-fallbacks` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** you introduce a new internal API while old callers still exist.

Move every caller and delete the old path in the same change. No dual paths, no compatibility layer kept "for now".

- Update the tests to assert the new contract; delete tests that only protected the old implementation.
- It applies when no external user depends on the old API and the project can take a coordinated breaking change, as part of a simplification or refactor.

If the deletion removes behavior someone relies on, or the API is published, it is a one-way door: sketch two options and get the owner's approval first ([PRINCIPLES.md](../PRINCIPLES.md), `doors`).
