---
name: catch-me-up
description: "Catch me up on what happened since my last message: what waits on me first, then a short account, then numbered decisions with your pick. Use when I say 'catch me up', 'qué pasó', 'en qué vamos' or 'cómo vamos'."
disable-model-invocation: true
---

# catch-me-up

Catch me up on what happened in this conversation since the last message I wrote before asking you to catch me up, then lay out every decision waiting on me so I can make it from your reply alone. Notifications and worker results are not my messages. Assume I remember my last message and nothing after it.

This is for me, about everything since my last message. `handoff` is written for another agent to continue the work; `wait-what` re-explains only your last message.

## Gather

Keep this turn read-only: change no code, start no job, post nothing. Collect your own work since my message, every worker's report, and anything anyone else wrote here. Read the current state of each pull request, check run and worker this conversation touched; do not repeat what a report said an hour ago.

A decision is something only I can do: a product or preference call, approving a one-way door, sending or publishing something, access you lack. Include any still owed from before my last message. Work you can still do yourself is not a decision. Never invent one to fill the reply.

## Write

1. Open with one sentence that names what waits on me, or says "nothing waits on you". If nothing happened either, say so and stop.
2. What happened, under 100 words: one short bullet per thing I asked for, saying what came of it and whether it is done, running, failed or not checked. Never mark done something that still waits on me. Name any bug found and fixed on the way. Say it in plain words; leave out hashes, paths and commands unless I need one to act.
3. One line for work left that you will do yourself.
4. The decisions, numbered, about 30 words each: the call, your pick, what it changes, the evidence behind your pick and its risk, with the link to the pull request or issue it is about. A call between options letters them, each with what it gets me and what it costs, so I can reply "1 sí, 2 B".

Label every claim measured (you ran it now, and what came out) or inferred (you read it in a report, code or docs; say where). Write "unknown" for a fact a decision needs that you could not read.

Never cut a decision or shrink it to stay under a word count: no count in place of a list, no link in place of an item. The reply grows with the decisions; the account of what happened does not.

Principles (the kitchen's PRINCIPLES.md): `owner-attention`, `evidence`, `truthful-state`.
