---
name: formal-verification
description: "Give a project a formal check of its riskiest promise (a model checker or verifier over every case in a stated scope), leave a project-local formal-<area> skill that runs it, and keep that check in step with the code. Use when I say 'model-check this', 'prove this invariant', 'verificación formal' or 'formal verification', or after code changes under a formal-<area> skill."
disable-model-invocation: true
---

# formal-verification

A test checks written cases; a formal check covers every state ordering or input inside a stated scope. Use it only for a high-harm promise tests rarely break, and only while the model matches the code. Read each linked section of [reference.md](reference.md) before the step that names it.

## Pick the mode

- **create**: no `formal-<area>` skill exists for this promise.
- **keep in step**: a `formal-<area>` skill exists and covered code changed, or `drift` is red.

## Edit scope

Edit verification files only: the model, its configs, `bin/formal`, controls, and the `formal-<area>` skill. Never edit product code unless the owner asks. If current code breaks the property, report it as a finding; never weaken the property, model, or bounds to pass. The project's own rules (commit-msg hook, attribution, `bin/check` tiers) win over this skill and any brief.

## Create

1. **Read the project:** AGENTS.md, `decisions.md`, its verify skill, `bin/check`, and hooks. Note commit rules.
2. **Choose the target** ([Target](reference.md#target)). Pick the highest-risk promise where concurrency, retries, ordering, permissions, money, or sync meet. Write the promise, the harm, and the code that keeps it (`file:line`). Ask the owner only what the code cannot answer.
3. **Choose and pin the tool** ([Tool](reference.md#tool)). Use a model checker (TLA+/TLC default) for event orderings, or a code verifier for single-function input properties (say so if none exists). Pin version and SHA-256 inside the project.
4. **Model with citations** ([Citations](reference.md#citations-and-the-coverage-record)). Cite `file:line` on every action. Map one indivisible code step to one model step, use repo names, and declare all bounds and assumptions in config.
5. **Budget the state space** ([Budget](reference.md#state-space-budget)). Start minimal, measure states and time, grow one bound at a time, and keep one config per question. Stop any run past its time or disk budget; never wait it out.
6. **Prove it can fail** ([Controls](reference.md#controls-mirrored-mutations)). Store each control as a rerunnable mirrored mutation (code edit + matching model edit). Turn every property red on at least one control and verify the trace contains every expected step. Then run current code: PASS, or a finding told as plain steps.
7. **Probe every bound** ([Boundary](reference.md#boundary-probes)). Write expected outcomes first, then run one probe inside each bounded assumption and one just past it.
8. **Write the project skill** ([Project skill](reference.md#the-project-skill)). Add `formal-<area>` next to the verify skill with `bin/formal check|drift|boundary|chart` and per-control commands. Generate the coverage record with `drift --record` and show `drift` red on every failure mode, including an uncommitted edit. Confirm the chart shows the counterexample's decisive step ([Chart](reference.md#the-chart)).
9. **Run a cold agent.** Brief a fresh worker in chef mode with only `formal-<area>`: run `check`, turn one control red, plant a scratch edit under a cited source file to see `drift` go red for that reason, and list every gap. Fix all gaps and rerun the failing step.
10. **Propose wiring.** Ask as a numbered decision (unless already settled) to put `drift` in `bin/check integrate` and `check` in `nightly`.
11. **Ship.** Open one PR with verification only, review with `second-opinion` (`../second-opinion/SKILL.md`), and report ([Report](reference.md#report)).

## Keep in step

1. **Locate checks:** read each `formal-*` skill (or the named one), noting its property, coverage record, bounds, commands, and derived sync commit.
2. **Run `bin/formal drift`** first. If green, run `check` (or cite a nightly record on this SHA); on pass, outcome is **clean**.
3. **Read all changes** to covered code since the sync commit (`git diff <sync>`, committed or not) plus new writers `drift` names. Classify each as: no model impact, model drift, intentional promise change (cite evidence), or possible bug.
4. **Update from code first:** update the model and citations before rerunning. Keep existing bounds unless the change invalidates one, and state any reduction.
5. **Rerun `check`.** On pass, rerun the control nearest the changed code (updating its mirror first if code moved). On red, replay the trace step by step against real code: an impossible step is a model error (fix and state what was wrong); a real trace is a **bug** (name the introducing commit and steps).
6. **Move the sync commit:** run `bin/formal drift --record` and commit it only once the model matches current code, never while blocked.
7. **Ship and report:** keep the model update in its own commit (inside the code PR or its own PR once code lands). Report bugs as product work, never fixed in the verification commit. Outcomes:
   - **clean**: nothing modeled changed, and `check` passes.
   - **updated**: model matches new code, `check` passes, and a control still goes red.
   - **bug**: model matches code, and code breaks the promise (may pair with **updated**).
   - **blocked**: check could not be aligned or run, with exact reason.

If an earlier pass turned red after updating the model, state clearly that the old pass described obsolete code.

## Reply

Report: outcome; promise and harm; tool and rationale; bounds with measured states and times; each control and its red; each boundary probe; findings as plain steps; uncovered scope; numbered decisions owed. Label every claim measured or inferred.

Principles (the kitchen's PRINCIPLES.md): `prove`, `design`, `truthful-state`, `evidence`.
