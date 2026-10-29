---
name: find-the-cause
description: "Find and fix the root cause of a bug, failing test, wrong output or slowdown: a red-capable repro first, falsifiable hypotheses, and the red run shown before the fix. Use when something is broken, throwing, failing or slow and the cause is not known yet."
disable-model-invocation: true
---

# find-the-cause

Every shipped line traces to runtime evidence. A change that "might help" is a hypothesis, not a fix, and it does not ship.

## 1. Red

Build one command that goes red on this exact symptom, and run it. It must be red-capable (drives the real code path and asserts the reported symptom), deterministic, fast, and runnable without me. Try in order: a failing test at the nearest seam, the project's verify skill, an HTTP or CLI script, a replayed captured payload, old-versus-new differential, `git bisect run`.

No red command, no hypotheses. If you cannot build one, stop, list what you tried, and ask for access or a captured artifact.

## 2. Minimise

Cut inputs, steps and config one at a time, rerunning the red command after each cut, until every remaining element is load-bearing.

## 3. Hypothesise

Write 3 to 5 ranked hypotheses, each falsifiable: "If X is the cause, changing Y makes the bug disappear." Show me the list; continue with your ranking if I am away.

## 4. Instrument

One variable at a time. Tag every debug log with one prefix, such as `[DEBUG-a4f2]`, and remove them all with one grep at the end. When evidence refutes a hypothesis, revert what it motivated. If two fixes failed on the same premise, attack the premise: write it down and test it before a third fix.

## 5. Fix

Only when fixing is authorized; otherwise stop and report the cause.

- Show the red run before writing the fix: the failing test or red command, its output trimmed to the assertion. It must fail for the right reason.
- Commit the failing test on its own first only when the repo's hooks allow a red commit. When a pre-commit or pre-push hook runs the tests, do not bypass it: commit test and fix together and put the red run in the PR description.
- Commit the smallest fix the evidence justifies, then grep for the same pattern and fix every instance.
- Prefer no new test over a tautological one: expected values come from an independent source, never recomputed the way the code does.
- Rerun the red command: green. Run the project's verification for the affected flows. Inconclusive, or green on a different surface, is not a pass.
- For a guard or verifier, replay the known counterexample and test a distinct bypass. Include a control that removes execution or its effect. Reverting the fix proves regression sensitivity, not completeness.

## Reply

What broke, the root cause and the evidence that proves it, the fix, the red and green output trimmed to the assertion, and what remains unverified. Then say whether this class of bug can be made impossible, and at which layer: code, guard or lint, hook, or rule. When the cause took a deep dig to find, propose the cheap, always-on signal (a log line, a status field, an assertion) that would have shown it at first look.

Principles (the kitchen's PRINCIPLES.md): `root-cause`, `prove`.
