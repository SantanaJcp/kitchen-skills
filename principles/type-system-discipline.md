# Type System Discipline

- **Parent:** `design` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** designing types or a signature, or writing code in any typed language.

Make the compiler reject impossible states, mismatched primitives and unhandled variants. Define errors and special cases out of existence rather than adding handlers for them.

- **Make illegal states unrepresentable.** If you can write a comment explaining when a combination of fields is valid, use a sum type.
- **Brand semantic primitives.** A user id and an order id are both strings but must not be interchangeable: give each its own type, validate once at creation, trust it downstream.
- **External data is untyped until parsed:** payloads, JSON, CLI arguments, config, environment variables, database rows. Parse each at its boundary into the typed model.
- **Derive types from the schema that owns the shape** (OpenAPI, protobuf, GraphQL, a migration) instead of hand-writing a parallel type.
- **Exhaust variants:** a switch over a union handles every case, and adding a case breaks the build where it is unhandled.
