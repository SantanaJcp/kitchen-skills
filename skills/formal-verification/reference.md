# formal-verification: reference

The detail behind each step of [SKILL.md](SKILL.md). It was written from the first trial: a TLA+ model of a promise that spans five components in a product whose language has no verifier. The numbers below are that trial's, measured there.

## Target

- A good target is a promise, not a module: "a report is never published as complete while one of its inputs is missing". It spans every component that can break it, whichever files they live in.
- Rank the candidates by harm times how rarely tests reach the breaking case. Interleavings, retries, partial failures, clocks and fences are where tests rarely go.
- Write three lines before any model: the promise, the harm when it breaks, and the components that keep it, each with `file:line`. If you cannot write the promise in one plain sentence, the target is not chosen yet.

## Tool

- Order of events across processes, retries or crashes: a model checker over a small model of the design. TLA+ with TLC is the default; it is language-agnostic.
- Behavior of one function for every input (overflow, memory safety, a data-structure invariant): a verifier that runs on the code. When none exists for the language, say so. Then either model the design, or test the real code with generated inputs, never a hand copy of the function.
- A property that must hold at every size, where a bound is not enough: a proof. That is rarely worth it; say why if you choose it.
- Pin the tool inside the project: the exact version, its SHA-256, and a path under the project's tools directory that git ignores. `bin/formal` verifies the checksum before each run and fails loudly on a mismatch; it never downloads a substitute. The runtime (a JDK for TLC) comes from the package manager, at the version you ran.

## Citations and the coverage record

- Every action in the model carries a comment that cites the code it stands for, in one greppable form, for example `\* code: path/to/file.ext:123 FunctionName`. One indivisible step in the code (one transaction, one lock-held block, one atomic write) is one action. Two steps in the code are two actions, even when splitting them grows the state space.
- The coverage record is derived, never hand-kept. `bin/formal drift --record` writes it from the model and the tree, and nothing else does. A hand-kept list missed a cited file in the trial; a reviewer caught it.
- The record also names the writers: every place in the source that writes the guarded state, found by a pattern (the table, field, key or topic names). A writer the model does not cite is either modeled, or listed as out of scope with the reason.
- For every covered file (each cited file, and each file holding a listed writer, out-of-scope ones included), the record keeps its content hash, `git hash-object <file>`, as it was when someone confirmed that the model describes it.
- The sync commit is derived too: the last commit that changed the record (`git log -1 --format=%H -- <record>`). It survives squash merges and rebases, and no one writes a SHA by hand. Keep in step reads the changes since it.

`drift` fails, with no model checker and no runtime, in under a second, when:

1. a covered file in the working tree, committed or not, no longer matches its recorded hash;
2. a file the model cites is missing from the record, or the record lists one the model no longer cites;
3. the writer pattern finds a writer in the source directories that the record does not list;
4. the record has uncommitted changes, so no commit vouches for it.

Show each failure red once, on a scratch change, before you hand the skill over.

## State-space budget

The trial's first model wrote 14 GB of state to disk and never finished. The one that shipped checks 22.3M states in 63 s and 12.5M in 35 s.

- Set the budget before the run: wall time and disk per config. A run past it is stopped, recorded as **incomplete** and shrunk. It is never left to finish, and never reported as a pass.
- Start from the smallest config that can show the bug: one of each actor, bounds of 1 or 2. Measure the distinct states and the time, then grow one bound and measure again. The growth tells you what the next bound will cost.
- One config per question. A config that asks two questions pays for their product.
- Remove variables nothing reads, and history variables no property needs.
- Use symmetry for interchangeable actors and values, but not in a config whose property names one of them.
- Bound with a state constraint only what the promise does not depend on, and declare every bound as an assumption the boundary probes test.
- Record in the project skill, per config: the bounds, the distinct states, the time and the machine.

## Controls: mirrored mutations

- A control is a named, stored edit to the real code, together with the same edit to the model: a patch pair, or a model flag the config sets, next to the code patch. Its name says what it breaks: `publish-before-fence`, `retry-without-idempotency-key`.
- Pick each edit as a plausible future bug: a swapped pair of lines, a removed check, a widened lock gap, a retry without its key. Every property needs at least one control that turns it red.
- `bin/formal control <name>` applies the model side in a scratch copy, runs the smallest config that should catch it, and passes only when the expected property goes red. The real-code side is kept so a reviewer can see that the mirror matches it. Each of the trial's controls went red in 2 to 4 s.
- The mirror can be wrong too. In the trial, one mirror dropped a step and produced a false counterexample. Before you trust a red, list the steps you expect its trace to contain, the decisive one included, and check that each is there in that order. A red with a missing or extra step is a mirror bug: fix the mirror, not the property.
- Tell each trace as a few numbered steps in the code's words: who did what, and what the reader then sees. In the trial, a two-line swap read as four steps.

## Boundary probes

- Every assumption with a bound (at most N retries, a clock skew under S, a batch of at most B) is probed on both sides: once inside it, expecting a pass, and once just past it, expecting the red that shows why the bound matters.
- Write the expected result of both probes before you run them. In the trial, the agent reasoned one side wrong; all three reviewers caught it, and a probe would have caught it first.
- A bound whose outside probe still passes is not load-bearing: drop it, or explain what it saves.
- `bin/formal boundary` runs every probe and prints, for each: the assumption, the side, the expected result, the actual result.

## The chart

- The raw state graph is unreadable. The chart is a projection: a function from a model state to a few labels in the reader's words ("waiting for the fence", "published, complete"). Steps become arrows labeled as the reader sees them happen.
- Aim for about 20 arrows. Merge states that look the same to the reader, but never two states that break different rules, and never a state on a counterexample path.
- Check the projection against the counterexample: its decisive step (the one the control's edit made possible) must be an arrow you can see. The trial's first projection hid it inside a merged state.
- Every chart state stays reachable from a start state. Hiding an arrow hides all of its steps or none, and the hand-off names what was hidden.
- Emit Mermaid `stateDiagram-v2` text from the projection with the project's own tooling; the kitchen ships no renderer. `bin/formal chart` writes the text, and the project skill says where.

## The project skill

`formal-<area>` sits next to the project's verify skill and stands alone for an agent who has never seen this work. It holds:

- frontmatter naming the area and when to use it (any change to a cited file or a writer of the guarded state);
- the promise and the harm, one plain sentence each;
- the commands: `bin/formal check` (each config; the tool's version and checksum verified), `drift`, `boundary`, `chart`, and `control <name>` for each control. Each command prints progress, ends in PASS or FAIL, and exits non-zero on anything but PASS;
- per config: its question, bounds, distinct states and time;
- the controls, each with its expected property and steps;
- the assumptions and their probes; what the model leaves out;
- where the coverage record lives, what it is derived from (the model's citations and the writer pattern), and how to read its sync commit;
- how to tell a bug from a modeling error (replay the trace against the code), and when to rerun `drift --record`;
- the full run's measured time. The trial's took 1 min 48 s, which fits a nightly tier, while `drift` fits the integrate tier.

## Report

- **create:** PASS (with no finding), finding (the current code breaks the promise) or BLOCKED. Then the promise and harm, the tool and why, each config with its states and time, each control with its red and its time, each boundary probe, what the cold agent found and what was fixed, the chart, what is not covered, and the wiring decision.
- **keep in step:** clean, updated, bug or blocked, per check. Then the commits read, each change classified, what changed in the model and why, the rerun and the control that still goes red, each bug as plain steps with its introducing commit, and the new sync commit or why the record did not move.
- Every number is labeled measured, with the command that produced it.
