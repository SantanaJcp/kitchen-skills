# Guard the Context Window

- **Parent:** `small-owned` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** context is filling up: large outputs, long files, repeated reads, planning a fan-out.

An overfull context degrades reasoning, loses detail at compaction and stalls the work. What an agent does not read, it does not follow.

- Route bulk reading and raw payloads to workers; keep their conclusions, not their dumps.
- Size each phase and cap its scope: a limit on files per phase, a turn budget, the cost of the mechanism counted.
- Read only the part you need, and only when you need it: a principle's leaf at the moment of use, not every file up front.
