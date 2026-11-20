---
name: second-opinion
description: "Cross-model review of a diff or design before merge: one run per reviewer from ~/.config/kitchen/models.toml with Skeptic, Architect, and Minimalist lenses plus all 13 principles in one prompt, triaged by the lead on a two-round budget. Use before merging or opening a PR, when asked for a review, and always on auth, tenant scope, sync, data, or migrations."
disable-model-invocation: true
---

# second-opinion

A model from another vendor checks the final work. The lead filters findings instead of forwarding raw noise.

## 0. Author and mode

- Identify the authoring model and its vendor (`claude`, `codex` for OpenAI/GPT, or `gemini`) from `Co-Authored-By`, the brief, or the delegator. State both in one line. If unknown, treat both as your own model and vendor.
- If another lead launched you as a reviewer, review and return. Launch no reviewers.
- Pick a mode: **Diff** (resolve base and head SHAs, run `git diff --stat <base>...<head>`, fail on empty diff), **Design** (pin SHA and exact files or folders), or **Design+diff** (both, with Design findings reported separately).
- State intent in two lines and quote the originating request.

## 1. Reviewers (one run per reviewer)

Read `~/.config/kitchen/models.toml` directly (`[[reviewer.<vendor>]]`, where `<vendor>` is `claude`, `codex`, or `gemini`; multiple entries form a panel).

- **Refuse same-vendor review:** judge self-review by the model's vendor, not the provider name alone. `claude-*` (including `claude-fable-*`) is `claude`, `gpt-*` / `codex` is `codex`, and `gemini-*` is `gemini`. A multi-model provider such as `antigravity` running a `claude-*` model is still vendor `claude` and stays refused for a `claude` author.
- Run **one** review pass per configured reviewer in parallel, saving each output to `$DIR/review-<n>.md`. If you are already from another vendor than the author, act as one reviewer yourself and launch only the other configured entries.
- **T3 Code:** launch one async `delegate_task` per reviewer (`role: "review"`, distinct `clientRequestId`). Match the provider instance from `orchestrator_capabilities` (`claudeAgent`, `codex`, or `antigravity`). Map the entry's effort setting to that provider's option (`effort` for `claudeAgent`, `reasoningEffort` for `codex`, `thought_level` for `antigravity`), plus `tier` when supported.
- **CLI:** run `codex exec --skip-git-repo-check -s read-only ... -o "$DIR/review-<n>.md" "<prompt>"` or `claude -p "<prompt>" > "$DIR/review-<n>.md"`. Providers without a read-only CLI path run through T3 Code.
- **Fallback, said aloud** (`no-fallbacks` exception): if no reviewer is configured for the author's vendor, or a configured reviewer is refused or unreachable, run the review on the session model (`$DIR/review-<n>-session.md`). Open the reply with `not a cross-model review`, naming each missing or failed reviewer and its error. Confirm every output file is non-empty before reading; a missing review is a gap, not a pass.

## 2. Single reviewer prompt

Fill the placeholders and send one read-only prompt per reviewer:

```
You are reviewing a <code change | design | design+diff>. Review only; do not edit files.
Intent: <two lines>
Originating request: <quoted request>
Repo: <path> | Pinned: base <full SHA>, head <full SHA>; inspect `git diff <base>...<head>` (or Design files at <SHA>).
Checkout: read at pinned head only (<worktree> or `git show <sha>:<path>`). Do not switch branches in a shared checkout.
Forbidden: edits, commits, checkout/switch, reset, stash, push, merge, installs, deploys, migrations, mutating network calls.
Authorized tests: <exact commands or "none">. Project rules: <repo>/AGENTS.md.

Apply all three lenses in one pass:
- Skeptic: inputs, states, or orderings that break; swallowed errors; unproven claims; tests that pass on broken behavior or assert implementation or recomputed expectations.
- Architect: structural fit, leaking responsibilities, scale, concurrency, or ordering assumptions.
- Minimalist: what can be deleted, single-call abstractions, reader load (>3 files to trace a value).

Check against the 13 kitchen principles (every finding MUST name the principle id it violates):
`prove`, `truthful-state`, `no-fallbacks`, `root-cause`, `encode-lessons`, `owner-attention`, `doors`, `evidence`, `small-owned`, `isolate`, `cross-review`, `handoff`, `design`.

Report separate sections:
## Standards (rule violations; cite the rule)
## Request (missing, partial, unrequested, or wrong; in a repo with a verify feature map, a behavior change without a live drive + assertion there is a Request finding)
## Design (design and design+diff only)

Every finding cites `file:line`, a concrete failure path, measured (command + result) or inferred, and the principle id it violates. Mark merge-blocking issues BLOCKER (P0/P1). No findings is a valid result: say "no findings" when clean, and skip style nits.
```

## 3. Budget, triage, and merge danger

- **Two-round budget per PR:** round 1 is the open review above. Round 2 reruns **only** round 1's confirmed P0/P1 repros against the fixes.
- **Lead triage:** merge duplicate findings across reviewers (mark as agreed), verify each against the real code, keep axes separate, and bucket every finding with its axis, evidence, and principle id:
  - **Act on** (confirmed P0/P1. Fix only these before merge), **Consider** (optional/judgment), **Noted** (true, defer), **Dismissed** (with reason so the owner can overrule).
- **Merge danger:** one line each for two-way vs one-way door, blast radius, the key safety fact and how far it is proven (said -> pointed at line -> walked failure -> ran -> reproduced live), plus what remains unproven.

## Reply

Report in this order: Verdict (starting with `not a cross-model review` + errors if fallback ran); author model, vendor, and review route; Act on; Consider; Noted; Dismissed; Merge danger; reviewers run (route, provider/model/effort/tier, output files). Change no code during the review unless asked.

Principles (the kitchen's PRINCIPLES.md): `cross-review`, `evidence`, `no-fallbacks`.
