# Subtract Before You Add

- **Parent:** `small-owned` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** sequencing an addition, refactor or rewrite.

Remove complexity first, then build on the simpler base. Leave the design a little simpler and more capable, behind the same or a smaller surface, than you found it.

- Removal comes before construction; cut before you polish.
- Design for observed use, not speculative edge cases.
- No validators, parsers or guards beyond what the spec demands.
- Remove redundant instructions from prompts and skills.
- A reference or stub with nothing new in it gets deleted, not kept.
