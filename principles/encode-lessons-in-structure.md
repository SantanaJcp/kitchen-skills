# Encode Lessons in Structure

- **Parent:** `encode-lessons` ([PRINCIPLES.md](../PRINCIPLES.md))
- **When:** you catch yourself writing the same instruction a second time, or the owner repeats a correction.

Turn the rule into a lint, a metadata flag, a runtime check or a script, and delete the text. Fix it at the strongest layer: impossible in code, then a test or lint, then a hook, then a project AGENTS.md rule, then a skill, then memory.

- If the fix is structural, use only the structural fix. The instruction was the symptom.
- If the lesson needs judgment and cannot be encoded, make the instruction more prominent and add an example of the failure it prevents.

Three kitchen rules that used to be text are now hooks (PR #31).
