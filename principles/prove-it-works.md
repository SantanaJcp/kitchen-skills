# Prove It Works

- **Parent:** `prove` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** after a task, before you say it is done, fixed or safe.

Check the real thing, not a proxy. "It compiles", a file's mtime, a cached screenshot or a worker's summary is not proof.

- Read the actual value, not a derived or cached copy of it. Check a process is alive directly, not through state it left behind.
- When the check fails, suspect the way you observed before you suspect the system.
- Script the check when you can: a command that reruns the same comparison beats a one-time look. Keep its output so a reviewer reruns it instead of trusting your word.
- Keep the artifact where the owner can see it; commit it only when the trail must be auditable later, such as a large migration.

The kitchen's own review found controls reporting success they had not earned (PR #22).
