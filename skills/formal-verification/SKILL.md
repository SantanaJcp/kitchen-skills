---
name: formal-verification
description: "Give a project a formal check of its riskiest promise (a model checker or verifier over every case in a stated scope), leave a project-local formal-<area> skill that runs it, and keep that check in step with the code. Use when I say 'model-check this', 'prove this invariant', 'verificación formal' or 'formal verification', or after code changes under a formal-<area> skill."
disable-model-invocation: true
---

# formal-verification

A test checks the cases someone thought to write. A formal check covers every case in a stated scope: every order of steps in a model, or every input up to a bound. It is worth its cost only on a promise where a break harms people and tests rarely reach the case that breaks it, and only while the model still describes the code. This skill builds that check once (**create**) and keeps it true afterwards (**keep in step**). The details live in [reference.md](reference.md); read each section before the step that names it.

## Pick the mode

- **create**: the project has no `formal-<area>` skill for this promise.
- **keep in step**: a `formal-<area>` skill exists, and code changed under it, or its `drift` is red.

## Edit scope

The verification only: the model, its configs, the project's `bin/formal`, the controls and the `formal-<area>` skill. Never product code, unless the owner asks. When the current code breaks the property, that is a finding: never weaken the property, the model or a bound until it passes. The project's own rules win over this skill and over any brief (a commit-msg hook, the attribution it accepts, its `bin/check` tiers).

## Create

1. **Read the project**: AGENTS.md, `decisions.md`, its verify skill and `bin/check`, its hooks. Note what a commit must look like there.
2. **Choose the target from the code** ([Target](reference.md#target)). Look where concurrency, retries, ordering, permissions, money or sync meet. Write the promise in one plain sentence, the harm in one, and the code that keeps it with `file:line`. Choose the strongest promise, not the easiest to model. Ask the owner only what the code cannot tell you.
3. **Let the question choose the tool** ([Tool](reference.md#tool)). Whether steps can happen in a bad order: a model checker, with TLA+/TLC as the default. Whether the code holds for every input: a verifier that runs on the code. If no verifier exists for the language, say so. Pin the tool's version and checksum inside the project, and say why you chose it.
4. **Model with citations** ([Citations](reference.md#citations-and-the-coverage-record)). Every action cites the `file:line` it stands for. One indivisible step in the code is one step in the model. Names come from the repo, and bounds and assumptions are declared in the config.
5. **Budget the state space** ([Budget](reference.md#state-space-budget)). Start from the smallest config, measure, then grow one bound at a time. Split the configs per question. Stop a run that passes its time or disk budget; never wait it out.
6. **Prove it can fail** ([Controls](reference.md#controls-mirrored-mutations)). Name each control as a mirrored mutation: an edit to the real code plus the same edit to the model, stored and rerunnable. Each property goes red on at least one control. Before you trust a red, check that its trace has every expected step. Then the current code: PASS, or a finding told as plain steps.
7. **Probe every bounded assumption on both sides** ([Boundary](reference.md#boundary-probes)): one run inside the bound and one just past it, each with its expected result written down first.
8. **Write the project skill** ([Project skill](reference.md#the-project-skill)): `formal-<area>` next to the verify skill, with `bin/formal check|drift|boundary|chart` and one command per control. Write the coverage record with `drift --record`: the cited files, the writers and each covered file's hash, so the sync commit is derived. Show `drift` red on each of its failures, an uncommitted edit included. Check that the chart's projection shows the counterexample's decisive step ([Chart](reference.md#the-chart)).
9. **Run a cold agent on it.** Brief a fresh worker, in chef mode, that has only the project skill. Its tasks: run `check`, turn one control red, plant a scratch change under a cited file and see `drift` go red, and list every gap it hit. Fix each gap, then rerun the cold agent's failing step. In the trial, this step found the most gaps.
10. **Propose the wiring**: `drift` in `bin/check integrate`, because it is instant, and `check` in `nightly`. Editing the project's tiers is the owner's decision: ask it as a numbered decision unless the owner already decided it.
11. **Ship**: one pull request with the verification only, reviewed with `second-opinion` (`../second-opinion/SKILL.md`). Then report ([Report](reference.md#report)).

## Keep in step

1. **Find the checks**: each `formal-*` skill in the project, or the one named. Read it, and note its property, coverage record, bounds, commands and sync commit (the last commit that changed the record).
2. **Run `bin/formal drift`** before you change anything. Green means every covered file in the working tree still matches the record, and no new writer appeared: run `check` (or cite a nightly record of it on this SHA), and on a pass the outcome is **clean**.
3. **Read every change** to the covered code since the sync commit, committed or not (`git diff <sync>`), and every new writer that `drift` names. Classify each one: no effect on what the model describes; the model no longer describes the code; the promise changed on purpose (cite the evidence: a doc, a test, the commit message); or the promise may be broken.
4. **Update from the code, not from the result**: change the model and its citations before you rerun. Keep the bounds unless the change makes one wrong, and say so if you lower one.
5. **Rerun `check`.** On a pass, rerun the control nearest the changed code, and update its mirror first if the code moved, so you know the check can still fail. On a red, replay the trace step by step against the real code. A step the code cannot take means the model is wrong: fix it and say what was wrong. If every step happens in the code, it is a **bug**: name the commit that introduced it and tell the trace as plain steps.
6. **Move the sync commit**: rerun `bin/formal drift --record` and commit the record, only after the model describes the code as it is now. Never for a blocked check.
7. **Ship and report**: the model update is its own commit. It goes in the pull request that changed the covered code, so `drift` in that pull request's integrate tier goes green, or in its own pull request once that code has landed. A bug the check found is reported; its fix is product work, never part of the verification commit. The outcome is one of:
   - **clean**: nothing the model describes changed, and `check` passes;
   - **updated**: the model now describes the new code, `check` passes, and a control still goes red;
   - **bug**: the model describes the code, and the code breaks the promise (it can come with **updated**);
   - **blocked**: the check could not be brought back in line or could not run, with the exact reason.

A pass that came before an update and a red that came after it mean the old pass described code that no longer existed. Say so.

## Reply

The outcome; the promise and the harm in plain words; the tool and why; the bounds and the measured state counts and times; each control and its red; each boundary probe; each finding as plain steps; what the check does not cover; the decisions owed, numbered. Label every claim measured or inferred.

Principles (the kitchen's PRINCIPLES.md): `prove`, `design`, `truthful-state`, `evidence`.
