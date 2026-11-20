# kitchen-skills

My personal agent kitchen: small, owned skills plus the minimal tooling that keeps them honest, shared by Claude Code, Codex and Antigravity from one source. Nothing is always on: type `/chef-mode` (Claude Code, Antigravity) or `$chef-mode` (Codex) and the agent works the kitchen's way for that session.

> Built on ideas from [Poteto's pstack](https://github.com/cursor/plugins/tree/main/pstack) and [Matt Pocock's skills](https://github.com/mattpocock/skills), rewritten small and owned.

## Layout

| Path | What lives there |
|---|---|
| `PRINCIPLES.md` | The 13 principles every rule, hook, and check points at (pstack's 24 folded in) |
| `principles/` | One short leaf per nested principle, linked from `PRINCIPLES.md` and `chef-mode` |
| `decisions.md` | Decisions about the kitchen that outlive a session |
| `skills/chef-mode/` | The mode: the 13 principles inline, doors and autonomy ladder, and playbooks routing to the other skills |
| `skills/<name>/` | Twelve manual-only skills, reached through `chef-mode` or by name |
| `bin/kitchen` | The CLI: `install`, `doctor`, `check`, `guards`, `init`, `retro` |
| `hooks/` | `kitchen-guard` (PreToolUse Bash guard) and `chef-mode-after-compact` (SessionStart compact reminder) |
| `automation/` | Minimal nightly guard (`nightly-guard`, `install-schedule`) |
| `bin/check`, `.agents/skills/verify-kitchen-skills/` | This repo's check contract and verify skill |
| `tests/` | Unit tests and attack corpora |

Project-specific skills (deploy, verification for one app) stay in that project's `.agents/skills/`.

## Install

Requirements: Python 3.11+ (stdlib only) and `git` for `bin/kitchen` / `lib/`; system `python3` (3.9+) for `hooks/`; `bash`, `lsof`, and `gh` for `automation/` (launchd on macOS, systemd user timers on Linux).

```bash
git clone https://github.com/SantanaJcp/kitchen-skills.git
cd kitchen-skills
bin/kitchen install   # links the skills into ~/.claude/skills, ~/.agents/skills and ~/.gemini/config/skills (Antigravity), and bin/kitchen into ~/.local/bin
bin/kitchen doctor    # verifies links, python3, gh, and any nightly timer
```

Every installed path is a symlink back into this repo, so `git pull` updates them. `install` writes no global rules or global hooks; your `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` stay yours. It sets this clone's `core.hooksPath` to `.githooks` and refuses to overwrite unmanaged paths (`--backup` moves them to `~/.kitchen-backups/`).

Every kitchen skill is manual-only (`disable-model-invocation: true` for Claude Code and `allow_implicit_invocation: false` for Codex). In a project, how far an agent in chef mode may go on two-way doors is set by `Autonomy: propose|merge|ship` in that project's `AGENTS.md` (read from the shared base branch; default `propose`). One-way doors wait for owner approval on every rung.

## Agent hooks

Per-project hooks installed with `kitchen guards <repo>` (into `.kitchen/hooks/`, wired from `.claude/settings.json` and `.codex/hooks.json`; in this repo, settings call `hooks/` directly):

- `kitchen-guard`: one process per Bash call, fronted by a POSIX-sh prefilter that exits 0 without starting Python when a call mentions none of `git`, `rm`, or `gh`. Blocks:
  1. `git commit --no-verify` / `-n`, `--no-verify` on hooked git commands, and `core.hooksPath` overrides;
  2. recursive `rm` outside temp/scratch dirs (including per-user agent CLI scratch dirs like `claude-<uid>`);
  3. `git push` to `dev`, `main`, `master`, or patterns in `~/.config/kitchen/shared-branches.txt`;
  4. private denylist terms (`~/.config/kitchen/denylist.txt`) in text published from a kitchen checkout.
  Fails closed (exit 2) on unreadable input or an unparseable command touching `git`, `rm`, or `gh`. See `hooks/README.md` and `tests/corpus/hooks/cases.json` for known limits.
- `chef-mode-after-compact`: on `SessionStart` (`compact`), prints the 13 principles and tells the agent to re-read `chef-mode/SKILL.md` if it was in chef mode.

## Commands

```bash
kitchen install             # link skills and bin/kitchen
kitchen doctor              # check links, python3, gh, and nightly timers
kitchen check               # lint skills, scan for private data/secrets, run every test
kitchen guards ../some-repo # install kitchen-guard and chef-mode-after-compact in that repo (--check detects drift)
kitchen init ../some-repo   # print the init playbook for an agent to set up a repo
kitchen retro --since 7d    # extract your prompts from Claude Code, Codex, and retro-sources.txt
```

Nightly checks are scheduled per machine with `automation/bin/install-schedule <project> [hour]` (details in `automation/README.md`).

## Per-machine config (`~/.config/kitchen/`)

Keep personal config outside this public repo:

- `models.toml`: worker and cross-model reviewer setup, read directly by agents (`chef-mode`, `second-opinion`):

```toml
[worker]
provider = "claude"
model = "default"
effort = "high"
tier = "standard"
accounts = ["personal-1"]

[[reviewer.claude]]
provider = "codex"
model = "default"
effort = "high"
tier = "standard"

[[reviewer.codex]]
provider = "claude"
model = "default"
effort = "high"
tier = "standard"
```

- `denylist.txt`: private terms (one per line) blocked in this public repo by `kitchen check` and `kitchen-guard`.
- `shared-branches.txt` (optional): extra branch names or globs protected by `kitchen-guard`.
- `retro-sources.txt` (optional): extra local paths or `ssh <host> [user]` sources for `kitchen retro`.
- `automation/<project>.env`: project config for `automation/bin/nightly-guard`.

## Skills

| Skill | Use it when |
|---|---|
| `chef-mode` | Switch the kitchen on for a session; `/chef-mode init` sets a repo up |
| `make-me-realize` | Settle open decisions through numbered questions before designing |
| `feature-xray` | Read-only investigation ending in decisions |
| `find-the-cause` | Something is broken or slow and the cause is unknown |
| `second-opinion` | Cross-model review before merging (one run per reviewer, two-round budget) |
| `handoff` | Pass work to another agent, tool, or session |
| `catch-me-up` | Summarize what happened since your last message and what waits on you |
| `retro` | Turn repeated corrections into environment fixes (manual) |
| `wait-what` | Re-explain the last answer simply |
| `sharpen` | Make a prompt deeper, not bigger, before starting |
| `audit-verification` | Prove a project's `verify-*` skill still tells the truth (manual) |
| `formal-verification` | Model-check a high-risk invariant and keep it in step with code |

## License

Apache-2.0
