# kitchen-skills

My personal agent kitchen: small, owned skills plus the tooling that keeps them honest, shared by Claude Code and Codex from one source. Nothing is always on: type `/chef-mode` (Claude Code) or `$chef-mode` (Codex) and the agent works the kitchen's way for that session.

> Built on ideas from [Poteto's pstack](https://github.com/cursor/plugins/tree/main/pstack) and [Matt Pocock's skills](https://github.com/mattpocock/skills), rewritten small and owned.

## Layout

| Path | What lives there |
|---|---|
| `PRINCIPLES.md` | What we do and why: the 13 principles every rule, hook and check points at (pstack's 24 folded in) |
| `principles/` | One short leaf per nested principle, linked from `PRINCIPLES.md` and from chef-mode's index |
| `decisions.md` | Decisions about the kitchen that outlive a session |
| `skills/chef-mode/` | The mode: the kitchen's rules, the trust ladder and the playbooks (`playbooks/init.md` and its starting points) that route to the other skills |
| `skills/<name>/` | One skill per folder: a short `SKILL.md`, plus scripts when a step is mechanical; all manual-only, reached through chef-mode or by name |
| `bin/kitchen` | The CLI: install, doctor, check, status, guards and the rest |
| `hooks/` | Agent guards, copied into each project that wants them and run before every shell command there, and the hook that brings chef mode back after a compaction |
| `automation/` | Scheduled jobs (nightly guard, weekly gardener, daily audit, weekly retro) |
| `bin/check`, `.agents/skills/verify-kitchen-skills/` | This repo's own check contract and verify skill |
| `tests/` | The repo verifies itself |

Project-specific skills (deploy, verification for one app) stay in that project's `.agents/skills/`.

## Install

What each part needs:

| Part | Needs |
|---|---|
| `bin/kitchen`, `lib/` | Python 3.11+ (standard library only) and git; `gh` for pull-request status |
| `hooks/` | `python3` on PATH (3.9+; macOS's `/usr/bin/python3` works) |
| `automation/` | bash and `lsof`; launchd on macOS, systemd user timers on Linux. The gardener's sandbox is `srt`, a Node package pinned in `automation/package.json`: Node.js, then `npm ci --ignore-scripts --prefix automation`. On Linux `srt` also needs bubblewrap, socat and ripgrep |

The automation tests need `srt` too: without it `kitchen check` stops with that one `npm ci` line.

The shortest way: tell any agent

> Install the kitchen from https://github.com/SantanaJcp/kitchen-skills: clone it to `~/Development/kitchen-skills` (or pull it if it is already there), run `bin/kitchen install`, then `bin/kitchen doctor`, and show me both outputs.

Or by hand:

```bash
git clone https://github.com/SantanaJcp/kitchen-skills.git
cd kitchen-skills
bin/kitchen install   # links chef-mode and the skills for Claude Code and Codex
bin/kitchen doctor    # proves the links, agent CLIs, automation tools and schedules
```

`install` links each `skills/<name>` into `~/.claude/skills/` and `~/.agents/skills/`, and `bin/kitchen` into `~/.local/bin/`. Every installed path is a symlink back into this repo, so a `git pull` updates them. It writes nothing that every session reads: no global rules file, no global hooks. Your `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` are yours. It also sets this clone's `core.hooksPath` to `.githooks`, the repo's own pre-commit hook. It does not change your PATH: add `~/.local/bin` to it if it is not there (`doctor` warns). It refuses to replace anything it does not manage; `--backup` moves those paths to `~/.kitchen-backups/` first.

Every skill is manual-only (`disable-model-invocation: true` for Claude Code, `allow_implicit_invocation: false` for Codex; `kitchen check` fails on a kitchen skill that is not, or when the two disagree). `/chef-mode` carries an index of the principles, has the agent read a principle's section or leaf (`kitchen principle <id|name>`) before applying it, switches the rules on for the session, and reads the other skills when a playbook step needs them. How far an agent in chef mode may go on its own is the project's rung on the trust ladder, a line in its AGENTS.md: `Autonomy: propose` (you merge; the default), `merge` (it merges its own reversible pull requests once the check is green, the verify method passed and the other model's review left no P0/P1) or `ship` (it also lands stacks unattended and deploys to the non-production environments that AGENTS.md names). The agent reads the rung from the shared base branch, never from the branch under review, and only you change it. One-way doors wait for you on every rung. A worker it delegates to edit, review or investigate gets a brief that starts by reading chef-mode. The kitchen registers no agent: Claude Code can delegate to a registered agent on its own, and chef mode starts only when you ask.

Moving from an older install: `install` moves a rules file an older install generated (and the old `~/.config/kitchen/rules.txt`) to `~/.kitchen-backups/`, and removes the kitchen's guards from `~/.claude/settings.json` and `~/.codex/hooks.json`, keeping every other setting and hook. A hook file it cannot parse is refused; `--backup` moves it aside. `doctor` and `status --exceptions` fail while any of it is left.

`doctor` fails when a link is missing or dangling, or something an older install left is still there. It warns when `claude` or `codex` is not on PATH, when no reviewer is set in `kitchen models`, when automation is configured but `node` or `srt` is missing, and when a kitchen LaunchAgent plist is present but not loaded (macOS) or a kitchen timer is not active (Linux). A job installed `--triggered` shows as triggered, with its last run.

## Agent hooks

Rules that used to be text, or lessons from incidents, are now guards, per project: they run only in repos that carry them in `.kitchen/hooks/`, from that repo's `.claude/settings.json` and `.codex/hooks.json` (`kitchen guards <repo>` puts them there). Each one blocks with a message that says what to do instead:

| Guard | Blocks |
|---|---|
| `deny-no-verify` | `git commit --no-verify` or `-n`, `--no-verify` on push, merge and other hooked commands, a `core.hooksPath` or `include.*` override on the command line, and, on `git commit`, options that come from a variable, a command output or `xargs` |
| `deny-shared-push` | `git push` to `dev`, `main`, `master` or a branch listed in `~/.config/kitchen/shared-branches.txt` (names or globs), including a bare `git push` from such a branch, `--all` and `--mirror`. Abbreviated options are read as the full one (`--mir` is `--mirror`). A push whose destination cannot be known statically blocks too: the matching refspec `:`, a remote from a variable or command output, arguments from `xargs`, or a `-c`/`GIT_CONFIG_*` that sets `push.*`, `remote.*`, `branch.*` or `include.*` |
| `deny-recursive-rm` | recursive `rm` on anything outside `/tmp` or `$TMPDIR`; use `trash <path>` instead |
| `deny-private-publish` | `gh pr` and `gh issue` `create`, `edit`, `comment` (and `gh pr review`, `merge`) or `git commit` reading its text from a fixed path in `/tmp`, `/var/tmp` or `/private/tmp`, as a body file, stdin or through `cat`: another local user may own that file. Send the text on stdin instead (`--body-file -` or `git commit -F -` with a heredoc). In a kitchen checkout only, also a publish command whose text holds a term from the private denylist (`KITCHEN_DENYLIST`, else `~/.config/kitchen/denylist.txt`), matched as `kitchen check` does |

They read through `VAR=x` prefixes, `command`, `env`, `sudo`, `xargs`, `find -exec`, `sh -c`, `eval`, subshells, `$(...)` and heredocs. The attack corpus in `tests/corpus/hooks/cases.json` lists what each guard blocks, what it allows, and the evasions it cannot see (git aliases, scripts in files, commands piped into a shell), so a gap is written down, not hidden. A guard that cannot read its input, parse the command or even load blocks with exit 2 (the only code both tools treat as a block) rather than passing silently.

`chef-mode-after-compact` blocks nothing: after a compaction (`SessionStart`, source `compact`) it reads the session's transcript, and when chef-mode was loaded (`/chef-mode`, `$chef-mode`, the Skill tool, or a read of its `SKILL.md`) it tells the agent chef mode is still on, names the `SKILL.md` to read again and hands back its `## Principles` index. A session without chef-mode gets nothing; a skill file that is gone is said, not skipped. Measured with Claude Code 2.1.312 across processes (`claude -p --resume` with `/compact`) and Codex 0.165 (auto-compaction): without it, a compaction drops chef mode.

## Daily use

```bash
kitchen status              # every project: branch, upstream, PRs, nightly guard, gardener, daily audit, agent checkpoints, owed decisions
kitchen status --exceptions # only the lines that are not green; exits 1 when there are any
kitchen log "msg" --status done   # agents leave checkpoints during autonomous runs (done, blocked, decision or note)
kitchen inventory           # skills, rule files and Codex automations the agents can see, and where copies drift
kitchen retro --since 7d    # my prompts from every Claude account, Codex and the hosts in retro-sources.txt, for the retro skill
kitchen models              # which models review each author's work and run delegated work; `kitchen models set|add|remove reviewer --author claude ...`, `kitchen models set worker [--project <name>] ...` to change them
kitchen integrate fix/a fix/b --base main   # merge in order in a disposable clone, run the project's checks after each merge
kitchen init ../some-repo   # print the instructions your agent follows to set a repo up (the same as /chef-mode init there)
kitchen guards ../some-repo # put the kitchen's guards in that repo only; --check fails while they drift
```

`init` is per repo, never automatic, and the work is your agent's: in the repo, type `/chef-mode init` (Claude Code) or `$chef-mode init` (Codex), or give any agent what `kitchen init <repo>` prints. The agent reads what the repo already has, asks you once, in numbered questions with its recommendation, which guardrails to add (the guards, a `bin/check` contract, a pre-commit hook with a secret scan, a verify skill, the `## Verify` and `Autonomy:` lines in AGENTS.md, `decisions.md`, and the repo in your `projects.txt` and `integrate.toml`), builds them in a worktree on `kitchen/init`, proves them (every check command run and seen to pass, the commit tier green twice and red on a planted defect in a file it runs, each guard blocking its probe), and opens one pull request that you merge. One-way doors, such as requiring the check on the shared branch, are printed as decisions with their commands, never run. The playbook and its starting points (`check.sh`, `pre-commit.sh`, `decisions.md`, a verify-skill template) live in `skills/chef-mode/playbooks/`.

`guards` copies `hooks/` into the repo's `.kitchen/hooks/` and runs each guard before every Bash call, and `chef-mode-after-compact` after every compaction, from the repo's `.claude/settings.json` and `.codex/hooks.json`, keeping every other setting and hook in them; a file it cannot parse is refused. A handler fails closed (exit 2) when its copy is missing, but lets everything through on a checkout without the settings file, so a session that loaded the guards and switched to a branch from before them is not locked out. Claude Code loads them in a session started at the repo root (measured). Codex resolves the repo from the git root, and runs a project hook only in a trusted project after you approve it in `/hooks`.

`integrate` reads check commands from `~/.config/kitchen/integrate.toml` (`[projects.<repo folder>]` with `base`, `checks = [...]` and optional `path`), prints PASS or FAIL bound to the exact SHA vector, and records it in `~/.local/state/kitchen/integrate/` with a digest of everything that decides what runs (checks, `path`, the base and every other key of the project's entry except `gardener`). There is no default base: without `base` or `--base` it fails. Checks that compare against `origin/<base>` see the base the batch merges onto: when the base is `origin/<branch>`, the clone gets that ref at the base SHA, copied from your repo, never fetched; a base given as a SHA or a local branch creates no `origin/` ref. `kitchen integrate <refs> --recorded` runs nothing and passes only if a recorded PASS matches the refs' current SHAs and that digest as configured now, so any moved HEAD or changed check configuration invalidates it. Nothing it runs inherits `GIT_*`.

`status` reads each project's scheduled-job config by running its `automation/<project>.env` with bash, so keep that file yours. A job with no config is informational; a configured job with no record is an exception. It starts with anything an older install left in your global setup (generated rules, global guards, `rules.txt`), as an exception for `--exceptions`, until `kitchen install` takes it out.

`models` is each person's own setup, in `~/.config/kitchen/models.toml`. Two roles. `worker` is one model for every delegated worker, with an optional ordered list of accounts (T3 provider instance ids): `get worker --job routine|judgment|hardest [--urgent] [--index N] --t3` prints the `delegate_task` target, the configured effort for judgment, one step lower for routine work and one higher for the hardest (between low and max), the fast tier only when urgent, and the account `accounts[N % len]`. A project can hold its own account list (`set worker --project <name> --accounts a,b`, `remove worker --project <name>`), which replaces the default list in that project only; `get worker` takes the project from `--project`, else from the current directory's git top-level folder (a linked worktree, such as T3's, gets the default accounts unless `--project` names its project), uses the default list outside any repository and says so on stderr, and `--json` names the project and which list it used. An account listed only under a project is never picked anywhere else, so a client's account serves that client's project alone; `--command` prints the CLI call, which edits and runs commands without asking. An unset worker runs on the session model, said on stderr with the command that sets it (the owner's one exception to `no-fallbacks`). `reviewer` is set per author family (`--author claude` means the work is Claude's, so its reviewers run another family's model). A reviewer is a list: `set` replaces it with one model, `add` appends another (a parallel reviewer), `remove` takes one out. Each has provider (`claude` or `codex`), model (`default` for the tool's own), effort and tier; another role is added when a workflow reads it. An unset reviewer runs on the session model too, said on stderr as not a cross-model review; a reviewer of the author's own family is refused; `get --command` prints each `codex exec` or `claude -p` call. `second-opinion` runs every lens on every reviewer from here: through `delegate_task` in T3 Code, their CLIs elsewhere; a reviewer that cannot be reached is replaced by the session model and the reply says it was not a cross-model review.

Scheduled jobs are installed per machine with `automation/bin/install-schedule`: `<project>` (nightly guard and weekly gardener), `--guard <project>` (the nightly guard alone), `--gardener <project>` (the gardener alone, for a machine that runs only it), `--audit <project>` (the daily verification audit, after the guard) or `--retro` (the weekly retro). launchd on macOS, systemd user timers on Linux. `--triggered` installs the audit, the gardener or the retro without its timer, for a T3 Code scheduled task to start with `automation/bin/run-job`: the job still runs in its own unit and sandbox (prompts in `automation/prompts/t3/`). Details in `automation/README.md`.

Per-machine config lives outside the repo, in `~/.config/kitchen/`: `projects.txt` (one project path per line), `integrate.toml`, `models.toml`, `denylist.txt`, `shared-branches.txt` (optional), `retro-sources.txt` (optional: where else `kitchen retro` reads my sessions, one per line, a local path or `ssh <host> [user]`, which runs `kitchen retro` on that host) and `automation/<project>.env` for the scheduled jobs. State (journal, nightly history, integrate records, retro reports) lives in `~/.local/state/kitchen/`.

## Skills

Every skill is manual: `/name` in Claude Code, `$name` in Codex, or read by chef-mode when one of its playbook steps needs it.

| Skill | Use it when |
|---|---|
| `chef-mode` | Switching the kitchen on for a session; `/chef-mode init` sets a repo up |
| `make-me-realize` | Before designing: settle open decisions through numbered questions |
| `feature-xray` | "Primero analicemos": read-only investigation that ends in decisions |
| `find-the-cause` | Something is broken and the cause is unknown |
| `second-opinion` | Before merging: cross-model review with triaged findings |
| `handoff` | Passing work to another agent, tool or session |
| `catch-me-up` | "Qué pasó": what happened since your last message, and what waits on you |
| `retro` | Weekly: turn repeated corrections into environment fixes |
| `wait-what` | The last answer did not land |
| `sharpen` | Before a task: make it deeper, not bigger, and rewrite the prompt |
| `audit-verification` | Periodically: prove a project's verify skill still tells the truth |
| `formal-verification` | A promise tests rarely reach: model-check it, leave a `formal-<area>` skill, keep it in step with the code |

## Check

```bash
bin/kitchen check          # lint skills, scan for private data, run every test
bin/kitchen check --fast   # the same lint and scan, plus only the tests the staged paths need
```

The pre-commit hook runs `check --fast`: a docs-only commit (outside `automation/`) runs only `tests/test_principles.py` (under a second: every control names a principle that exists), and the automation suite (about 95 s of the run) runs only when `automation/`, `lib/` or `tests/test_automation.py` is staged. It prints each module it skips. Plain `check` and `integrate` still run everything. Because this repo is public, `check` rejects personal absolute paths, credential shapes (GitHub, Slack, AWS and OpenAI-style tokens, JWTs, private key blocks, literal password or secret assignments) and any term listed in `~/.config/kitchen/denylist.txt` (one term per line, kept outside the repo). It scans the working tree and the exact staged blobs, so a secret staged and then deleted from the working file still fails.

Each detector (credentials, private terms, retro provenance, the agent hooks) is tested against its attack corpus in `tests/corpus/<detector>/cases.json`: positive, negative and bypass cases, read by detection and redaction alike. A miss found in review becomes a new bypass case.

## License

Apache-2.0
