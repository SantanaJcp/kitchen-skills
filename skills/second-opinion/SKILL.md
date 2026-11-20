---
name: second-opinion
description: "Cross-model review of a diff or design before merge: one run per reviewer from ~/.config/kitchen/models.toml with Skeptic, Architect, and Minimalist lenses plus all 13 principles in one prompt, triaged by the lead on a two-round budget. Use before merging or opening a PR, when asked for a review, and always on auth, tenant scope, sync, data, or migrations."
disable-model-invocation: true
---

# second-opinion

A model other than the author checks the final work; the lead filters findings instead of forwarding raw noise.

## 0. Author and mode

- Identify the authoring model from `Co-Authored-By`, the brief, or the delegator; say it in one line (unknown = treat as your own model).
- If you were launched as a reviewer by another lead, review and return; launch no reviewers.
- Pick mode: **Diff** (resolve base and head SHAs, `git diff --stat <base>...<head>`; empty diff fails), **Design** (pin SHA and exact files/folders), or **Design+diff** (both; report Design findings separately). State intent in two lines and quote the originating request.

## 1. Reviewers (one run per reviewer)

Read `~/.config/kitchen/models.toml` directly (`[[reviewer.<author>]]`, where `<author>` is `claude` or `codex`; several reviewers are allowed). Refuse any reviewer of the author's own family.

- Run **one** review pass per configured reviewer (all reviewers in parallel), saving each output to `$DIR/review-<n>.md`. If you are already a different model from the author, you are one reviewer yourself and launch only the other configured reviewers.
- **T3 Code**: one async `delegate_task` per reviewer (`role: "review"`, distinct `clientRequestId`), using the instance from `orchestrator_capabilities` matching `provider` (`codex` or `claudeAgent`) and the entry's `model`, `effort`, and `tier`.
- **CLI**: `codex exec --skip-git-repo-check -s read-only ... -o "$DIR/review-<n>.md" "<prompt>"` or `claude -p "<prompt>" > "$DIR/review-<n>.md"`.
- **Fallback, said aloud** (`no-fallbacks` exception): if no reviewer is configured, or one is refused/unreachable, run the review yourself on the session model (`$DIR/review-<n>-session.md`) and open the reply with `not a cross-model review` naming each failed reviewer and error. Confirm every output file is non-empty before reading; a missing review is a gap, not a pass.

## 2. Single reviewer prompt

Fill the placeholders and send one prompt per reviewer (read-only):

```
You are reviewing a <code change | design | design+diff>. Review only; do not edit files.
Intent: <two lines>
Originating request: <quoted request>
Repo: <path> | Pinned: base <full SHA>, head <full SHA>; inspect `git diff <base>...<head>` (or Design files at <SHA>).
Checkout: read at pinned head only (<worktree> or `git show <sha>:<path>`). Do not switch branches in a shared checkout.
Forbidden: edits, commits, checkout/switch, reset, stash, push, merge, installs, deploys, migrations, mutating network calls.
Authorized tests: <exact commands or "none">. Project rules: <repo>/AGENTS.md.

Apply all three lenses in one pass:
- Skeptic: inputs/states/orderings that break; swallowed errors; unproven claims; tests that cannot fail on broken behavior or assert implementation/recomputed expectations.
- Architect: structural fit, leaking responsibilities, scale/concurrency/ordering assumptions.
- Minimalist: what can be deleted, single-call abstractions, reader load (>3 files to trace a value).

Check against the 13 kitchen principles (every finding MUST name the principle id it violates):
`prove`, `truthful-state`, `no-fallbacks`, `root-cause`, `encode-lessons`, `owner-attention`, `doors`, `evidence`, `small-owned`, `isolate`, `cross-review`, `handoff`, `design`.

Report separate sections:
## Standards (rule violations; cite the rule)
## Request (missing/partial/unrequested/wrong; in a repo with a verify feature map, a behavior change without a live drive + assertion there is a Request finding)
## Design (design and design+diff only)

Every finding cites `file:line`, a concrete failure path, measured (command + result) or inferred, and the principle id it violates. Mark merge-blocking issues BLOCKER (P0/P1). Say "no findings" when clean; skip style nits.
```

## 3. Budget, triage, and merge danger

- **Two-round budget per PR**: round 1 is the open review above; round 2 reruns **only** round 1's confirmed P0/P1 repros against the fixes.
- **Lead triage**: merge duplicate findings across reviewers (mark as agreed), verify each against the real code, keep axes separate, and bucket every finding with its axis, evidence, and principle id:
  - **Act on** (confirmed P0/P1 — only these are fixed before merge), **Consider** (optional/judgment), **Noted** (true, defer), **Dismissed** (with reason so the owner can overrule).
- **Merge danger**: one line each for two-way vs one-way door, blast radius, the key safety fact and how far it is proven (said -> pointed at line -> walked failure -> ran -> reproduced live), plus what remains unproven.

## Reply

Verdict (starting with `not a cross-model review` + errors if fallback ran); author model and review route; Act on; Consider; Noted; Dismissed; Merge danger; reviewers run (route, provider/model/effort/tier, output files). Change no code during the review unless asked.

Principles (the kitchen's PRINCIPLES.md): `cross-review`, `evidence`, `no-fallbacks`.
