# Agent hooks

One script per guard. Claude Code and Codex run each one before every Bash command (`PreToolUse`): the script reads the tool call as JSON on stdin, exits 0 to allow it, or exits 2 with a message on stderr that tells the agent what to do instead.

They are per project: `kitchen guards <repo>` copies them into the repo's `.kitchen/hooks/` and runs them from its `.claude/settings.json` and `.codex/hooks.json`, so they guard that project only. `kitchen install` adds none to your global settings, and takes out the ones older installs added.

`chef-mode-after-compact` is the one hook that is not a guard: it runs after a compaction (`SessionStart`, source `compact`), reads the session's transcript, and when chef-mode was loaded tells the agent to read its `SKILL.md` again, with the `## Principles` index in `additionalContext`. It prints nothing in a session without chef-mode, and exits 2 with the reason on stderr when it cannot read its input or the transcript (on `SessionStart` that blocks nothing; Claude Code shows it to the person). It blocks nothing, so it has no attack corpus; `AfterCompactTests` in `tests/test_hooks.py` feeds it transcripts.

`shellparse.py` is shared: it turns the command string into the commands it would run. Every guard has positive, negative, bypass and known-limit cases in `tests/corpus/hooks/cases.json`; a new guard or a newly found evasion starts there.

Try one by hand:

```bash
echo '{"tool_name":"Bash","cwd":"/srv","tool_input":{"command":"git push origin dev"}}' | hooks/deny-shared-push; echo "exit $?"
```
