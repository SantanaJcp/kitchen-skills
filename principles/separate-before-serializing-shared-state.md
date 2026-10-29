# Separate Before Serializing Shared State

- **Parent:** `isolate` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** concurrent actors might write the same file, branch, key or state object.

First ask whether they need the same mutable object at all. If not, remove the sharing. Instructions and conventions are not concurrency control.

1. Find the shared mutable state: files two actors both write, branches both push to, APIs both define and consume.
2. By default, remove the shared write target: give each actor its own file, key, branch or directory, and merge only where the results are read. Two workers writing their own field into one state file still share it; two state files do not.
3. Only when one shared target is a real invariant, serialize access in structure: a lock file, sequential phases, a single writer, compare-and-swap. "We need a lock" is a design smell to check, not the default.

Measured here: `GIT_*` variables leaking from a worktree wrote junk commits and `core.bare=true` into the shared repo (PR #22).
