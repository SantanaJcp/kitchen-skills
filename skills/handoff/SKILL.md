---
name: handoff
description: "Write a handoff so another agent, tool or session (Claude, Codex, another machine) can continue this work without the conversation. Use when I ask for a handoff, to pass the work on, or for a prompt to paste into another session or machine."
disable-model-invocation: true
---

# handoff

Write the handoff to `${TMPDIR:-/tmp}/handoff-<repo>-<yyyymmdd-hhmm>.md`, never inside the workspace, and print the path. If the handoff is for another machine or for me to paste, include it in full in the reply because that path will not exist there. Tailor it to any arguments I passed describing the next session's task.

## Contents

1. **Goal**: what we are trying to achieve and how we will know it is done.
2. **State, measured now**: repo path, branch, HEAD SHA, `git status --short` summary, unpushed commits, open PRs (`gh pr list --author @me`), worktrees in use, anything still running.
3. **Done**: what is finished and how each piece was verified (command and result).
4. **Next**: ordered steps. The first one must be runnable without asking anyone anything.
5. **Decisions**: taken (point to `decisions.md` when it exists) and still owed by me.
6. **Traps**: what looks right but is not, and what not to touch.
7. **Verify**: the exact commands that prove the work.
8. **Suggested skills** for the next agent.

Reference specs, PRs, commits and logs by path or URL instead of copying them. Redact every secret.

## For the receiving agent

Before trusting the handoff, re-measure the state block: `git rev-parse HEAD`, `git status`, and the PR list. Report any difference before continuing. Once HEAD matches, trust verified items in Done instead of redoing finished work from scratch.

Principles (the kitchen's PRINCIPLES.md): `handoff`, `evidence`.
