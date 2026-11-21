---
name: no-comments
description: "Strip comment noise from a diff through the Comment Sicko subagent, fix accepted workaround or surprise flags at the root cause, and offer structural encodings for constraint comments. Use before review or when I type /no-comments or $no-comments."
disable-model-invocation: true
---

# no-comments

Spawn one fresh Comment Sicko subagent, audit its diff and report, fix accepted `MUST KILL` targets, and offer structural encodings for constraint comments.

## Scope

Use the caller's files or diff. Otherwise use the current diff against the base branch (default `main`), including the working tree.

## Steps

1. **Snapshot and spawn Comment Sicko.** Because scope includes uncommitted edits, this single sequential worker runs in the caller's checkout instead of a separate worktree (never in parallel with other writers). Snapshot the starting working tree first (`SNAP=$(git stash create)`; if empty, use `HEAD`). Then start one fresh subagent (worker model from `~/.config/kitchen/models.toml`, or the session model said aloud) with [comment-sicko.md](comment-sicko.md) and the scope. Pass the file path or its text verbatim; do not water down its rules.
2. **Audit its diff and report.** Inspect every edit and flag before accepting:
   - Reject application-code edits, out-of-scope edits, deletions of keep-list exceptions, misstated `MUST KILL` reasons, or flags condemning intentional code.
   - Restore a deleted comment only with exact keep-list exception proof. A reshape flag on a surprise in our own code stays actionable; do not restore its comment.
   - Audit missed lint or type suppressions in scope; correctness or safety suppressions stay actionable `MUST KILL`s.
   - For thin `IMPORTANT` or `do not remove` claims, trace the symbol and git/PR history before keeping or killing. If a keep is refuted or remains ambiguous, delete it; if a kill is ambiguous, do not restore it.
   - On a rejected report, restore only Comment Sicko's touched files from `$SNAP` (`git checkout "$SNAP" -- <touched files>`, preserving your prior uncommitted edits) and rerun once naming the failure. If a second report is rejected, restore from `$SNAP`, report it open, and fail `no-comments`.
3. **Fix accepted `MUST KILL`s.** Fix trivial flags directly (delete a dead path, drop an unused parameter, call the real API). If a fix reshapes a boundary or module, sketch two options first. Implement the smallest root-cause fix in scope (`root-cause`), removing every named workaround without widening scope or adding symptom guards. Report out-of-scope root causes open.
4. **Encode constraint comments.** For comments claiming `do not remove`, `do not change wording`, or `talk to X before changing` (outside external keep-list exceptions), propose the cheapest in-scope type, runtime check, test, or lint (`encode-lessons`) as a numbered decision. Wait for owner approval. If approved, encode then delete; otherwise delete, report the unenforced constraint open, and note any out-of-scope work.
5. **Verify.** Run the project's `bin/check commit` (or scoped tests) after any code or comment edits.

## Reply

Report: deletion count, restored comments, reruns, fixes landed, encoding offers and encodings applied, unenforced constraints, open out-of-scope work, and verification result.

Principles (the kitchen's PRINCIPLES.md): `small-owned`, `root-cause`, `encode-lessons`.
