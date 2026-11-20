---
name: audit-verification
description: "Manual audit that a project's verify-<repo> skill still tells the truth: the feature map matches the code, every feature is driven live, and the verifier can still fail. Ends clean, changed, findings, blocked or skipped. Use when I ask to audit verification."
disable-model-invocation: true
---

# audit-verification

A feature map starts to rot the moment the app changes, and a verifier that cannot fail is worse than none.

## Outcomes

Say which one:

- **clean**: a full pass covered every feature from source and live; nothing to change. A churn pass covering its scope says **clean (churn)** and lists its features; it claims nothing about the rest.
- **changed**: one PR with proven corrections to the verify skill.
- **findings**: drift or gaps not fixed in this pass (unauthorized or product bugs); report with evidence.
- **blocked**: coverage could not finish or a fix could not ship safely; say why.
- **skipped**: a churn pass found no change since the baseline SHA; never call it clean.

## Scope and edit rules

A **full** pass covers every feature file. A **churn** pass covers only features whose code or map file changed since a known baseline SHA (from the last audit report or PR), plus the churn sweep in step 1. Without a known baseline SHA, run a full pass. State the scope and baseline.

Edit only the verify skill's own folder, and only when fixes are authorized; never product code. When the map describes behavior the app no longer has, it is either doc drift (fix the map) or a product regression (report it; never hide it).

Keep three things separate in the report: **Inventory** (what the map lists), **Drives** (what is exercised live), and **Assertions** (what decides pass/fail and whether it can go red).

## Pass

0. **Locate** `.agents/skills/verify-*/`. None: stop and point to `../chef-mode/playbooks/init/verify/` and `../chef-mode/playbooks/init.md`. State the audited profile (environment, commit SHA, dataset, account/role, verifier flags); every result holds only for that profile.
1. **Inventory**: check the map's README against its files (fix missing, extra, duplicate, or dead entries). Sweep `git log <baseline>..HEAD` for user-facing surfaces missing from the map (require a concrete path). If a `formal-<area>` skill exists, run its `bin/formal drift`; red is a finding for `formal-verification`.
2. **Source wave** (capped at **4 workers** in parallel): assign feature files in scope to read-only workers (batch if more than 4 files). Each answers "how does this feature work for a user?" from source and returns: summary, source entry points, likely drift with `file:line` (or none), and one live recipe. Workers never drive the app or edit. Reconcile summaries, spot-check drift, and merge recipes into as few app states as practical.
3. **Drives**: exercise every feature in scope at least once. If the nightly guard already ran green on this exact SHA with the same profile, cite its record and coverage evidence and drive live only what it does not cover; a red record makes the outcome **blocked**. Hold three invariants:
   - health-check (`doctor`) before the first drive and after any failed drive; reset/relaunch if wedged;
   - evidence survives cleanup; verify it at its named path;
   - clean up everything a drive started after failures and at the end.
   Fix skill drift that causes `doctor` to fail and retry once before calling it blocked. An "unreachable" feature needs the concrete prerequisite and route attempted.
4. **Assertions**: for each feature, name the check that decides pass or fail. "No error" or "exit 0" alone is a gap.
5. **Can it fail?** Run at least one negative and positive control by hand on a feature in scope (in a scratch copy if the verifier has no control command):
   - **negative control**: inject a fault or nullify the execution/effect (skip the drive, stub the action to a no-op, drop the write) — the verifier must go red on that assertion;
   - **positive control**: unbroken copy, same command — must go green.
   Staying green under a nullified effect is the most important finding of the audit.
6. **Triage**: fix doc drift; fix harness gaps and re-drive live before shipping; report product bugs outside the PR. Never turn a product defect into a green expectation; use an explicit XFAIL referencing the defect if the verifier must keep running.
7. **Ship or stop**: for **changed**, re-read every changed file and open one PR. On a full pass, close any open audit findings issues that no longer reproduce (`gh issue close <n> --comment ...` naming the audited SHA). Keep run notes in scratch; never commit them.

## Reply

Outcome; scope and baseline; audited profile; nightly records cited; inventory drift; features driven and unreachable ones; features without real assertions; product gaps and any XFAIL added; each control run (command, expected, actual).

Principles (the kitchen's PRINCIPLES.md): `prove`, `truthful-state`.
