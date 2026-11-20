# Agent hooks

Two scripts, installed per project with `kitchen guards <repo>` (into `.kitchen/hooks/`, wired from `.claude/settings.json` and `.codex/hooks.json`; in this repo, those settings call `hooks/` directly).

- `kitchen-guard` (`PreToolUse` on `Bash`): one process per shell call, fronted by a POSIX-sh prefilter that exits 0 without starting Python when the call mentions none of `git`, `rm`, or `gh`. Blocks with exit 2 and an actionable message on stderr:
  1. `git commit --no-verify` / `-n`, `--no-verify` on hooked git commands, and `core.hooksPath` overrides;
  2. recursive `rm` outside temp/scratch dirs (including per-user agent CLI scratch dirs such as `claude-<uid>` under temp/cache roots);
  3. `git push` to `dev`, `main`, `master`, or a pattern in `~/.config/kitchen/shared-branches.txt`;
  4. private-terms denylist matches in text published (`git commit`, `gh pr/issue`) from a kitchen checkout.
  Fails closed (exit 2) on unreadable input or an unparseable command mentioning `git`, `rm`, or `gh`.
- `chef-mode-after-compact` (`SessionStart` on `compact`): prints the 13 principles (one line each) and tells the agent to re-read `chef-mode/SKILL.md` if it was in chef mode. No transcript parsing.

Attack corpus and known limits (`eval`, nested `$(...)`, `xargs`, `find -exec`, ANSI-C escapes, git aliases, scripts in files, piped shells): `tests/corpus/hooks/cases.json`.

Try it by hand:

```bash
echo '{"tool_name":"Bash","cwd":"/srv","tool_input":{"command":"git push origin dev"}}' | hooks/kitchen-guard; echo "exit $?"
```
