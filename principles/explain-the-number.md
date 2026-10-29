# Explain the Number

- **Parent:** `truthful-state` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** before you measure, and before you trust, report or act on a number you measured: a speedup, a regression, a latency, a count, an eval result.

- **Write the decision down before you run.** Name the result that would change a decision, and the noise floor. Stop when the spread between runs is larger than the effect.
- **Ask "why not double?"** Name what bounds the result: a core, a lock, the disk, the network, the load generator itself. Take it from a profile or counters read during a run, then map it to the source. A guess from reading code is not a limiter.
- **List what else the number could be measuring, and rule each out with evidence:** errors, skipped or cached work, code that never ran, a side left on default settings, noise, a piece too small to matter end to end.
- **Keep the evidence next to the number:** run count, spread and limiter, in the report or a linked artifact.

For an eval, ask the same of the trials: did every run do the task, does the gap hold across trials and models.

You skipped this when a run started without the result that would change a decision, when a number has no run count, no spread or no named limiter, or when the time saved is larger than the time the changed piece took.
