"""Tests for `kitchen retro` (Claude Code and Codex transcript extraction, provenance, and remote sources)."""
import json
import os
import unittest
from pathlib import Path

from tests.test_kitchen import KitchenFixture


class RetroTests(KitchenFixture):
    """Fixtures follow the real transcript formats: Claude Code project JSONL and Codex rollout JSONL."""

    def setUp(self):
        super().setUp()
        self.fakebin = Path(self.tmp.name) / "fakebin"
        self.fakebin.mkdir()

    def fake(self, name, body):
        path = self.fakebin / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)

    def claude_event(self, text, ts="2099-01-01T10:00:00Z", **extra):
        event = {"type": "user", "timestamp": ts, "cwd": "/work/shop", "entrypoint": "claude-desktop",
                 "promptSource": "sdk", "origin": {"kind": "human"}, "message": {"content": text}}
        event.update(extra)
        return event

    def write_claude(self, events, session="s1", folder="-work-shop"):
        path = self.home / ".claude" / "projects" / folder
        path.mkdir(parents=True, exist_ok=True)
        (path / f"{session}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    def write_codex(self, name, source, texts, **meta):
        folder = self.home / ".codex" / "sessions" / "2099" / "01" / "01"
        folder.mkdir(parents=True, exist_ok=True)
        payload = {"id": name, "cwd": "/work/api", "originator": "Codex Desktop", "source": source, "thread_source": "user", **meta}
        lines = [{"type": "session_meta", "timestamp": "2099-01-01T09:00:00Z", "payload": payload}]
        for entry in texts:
            text, client = (entry, True) if isinstance(entry, str) else entry
            item = {"type": "UserMessage", "id": f"u-{len(lines)}", "content": [{"type": "text", "text": text}]}
            if client:
                item["client_id"] = f"c-{len(lines)}"
            lines.append({"timestamp": "2099-01-01T10:00:00Z", "type": "event_msg", "payload": {"type": "item_completed", "thread_id": name, "item": item}})
        (folder / f"rollout-{name}.jsonl").write_text("".join(json.dumps(line, separators=(",", ":")) + "\n" for line in lines))

    def retro(self, *args):
        return self.kitchen("retro", "--since", "1d", "--json", *args, cwd=self.home)

    def texts(self, *args):
        return [p["text"] for p in json.loads(self.retro(*args).stdout)]

    def test_claude_keeps_human_prompts_and_drops_injected_turns(self):
        self.write_claude([
            self.claude_event("no, eso está mal otra vez"),
            self.claude_event([{"type": "tool_result", "content": "output"}]),
            self.claude_event("<system-reminder>context</system-reminder>"),
            self.claude_event("Context handoff (manual_context): moved"),
            self.claude_event("Act as the research sub-agent for this task. Do X"),
            self.claude_event("old prompt", ts="2000-01-01T00:00:00Z"),
        ])
        self.assertEqual(self.texts("--tool", "claude"), ["no, eso está mal otra vez"])

    def test_claude_excludes_by_provenance_including_cli_and_t3_launches(self):
        launched = "Without using any tools, answer in one line: which language do you reply in?"
        brief = "This thread starts a discussion with the owner about the kitchen. Work in your worktree."
        self.write_claude([
            self.claude_event("dale, sigue"),
            self.claude_event("Background task finished", origin={"kind": "task-notification"}, promptSource="system"),
            self.claude_event("Run unattended.", entrypoint="sdk-cli", origin=None),
            self.claude_event("Delegated task node:delegated-task:abc reached a terminal state. Use task_status.", origin=None),
            self.claude_event("replayed prompt", uuid="u-replay"),
            {"type": "assistant", "uuid": "a-replay", "parentUuid": "u-replay", "requestId": "req_syn_0001", "message": {"content": []}},
        ])
        self.write_claude([
            {"type": "queue-operation", "operation": "enqueue", "timestamp": "2099-01-01T10:00:00Z", "content": launched},
            self.claude_event(launched, entrypoint="sdk-ts", origin=None, cwd="/tmp"),
        ], session="probe", folder="-tmp")
        self.write_claude([
            self.claude_event(brief, entrypoint="sdk-ts", origin=None, cwd="/wt/docs"),
            self.claude_event("listame los principios", entrypoint="sdk-ts", origin=None, cwd="/wt/docs", ts="2099-01-01T10:05:00Z"),
        ], session="child", folder="-wt-docs")
        self.write_claude([
            {"type": "assistant", "timestamp": "2099-01-01T09:59:00Z", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": f"cd /tmp && claude -p '{launched}' --model x"}},
                {"type": "tool_use", "name": "mcp__t3-code__t3_thread_launch", "input": {"title": "Docs", "message": brief}},
            ]}},
        ], session="parent", folder="-work-lead")

        result = self.kitchen("retro", "--since", "1d", "--tool", "claude", cwd=self.home)
        self.assertEqual(self.texts("--tool", "claude"), ["dale, sigue", "listame los principios"])
        self.assertIn("2 prompts included · 6 excluded (notification 2, agent-launched 2, non-interactive 1, replayed 1)", result.stdout.splitlines()[-1])

    def test_heredoc_mentioning_claude_p_launches_nothing_and_unlinked_launch_is_unknown(self):
        brief = "This thread starts a discussion with the owner about the kitchen."
        self.write_claude([self.claude_event(brief, entrypoint="sdk-ts", origin=None, cwd="/wt/docs")], session="thread", folder="-wt-docs")
        heredoc = f"python3 - <<'EOF'\nLAUNCH = 'claude -p'\nBRIEF = '{brief}'\nEOF"
        self.write_claude([
            {"type": "assistant", "timestamp": "2099-01-01T10:20:00Z", "cwd": "/elsewhere", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": heredoc}}]}},
        ], session="editor", folder="-elsewhere")
        self.assertEqual(self.texts("--tool", "claude"), [brief])

        launched = "Without using any tools, answer in one line: which language do you reply in?"
        self.write_claude([
            self.claude_event(launched, entrypoint="sdk-ts", origin=None, cwd="/tmp"),
            self.claude_event("y en inglés?", entrypoint="sdk-ts", origin=None, cwd="/tmp", ts="2099-01-01T10:03:00Z"),
        ], session="probe", folder="-tmp")
        self.write_claude([
            {"type": "assistant", "timestamp": "2099-01-01T08:00:00Z", "cwd": "/srv", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": f"claude -p '{launched}'"}}]}},
        ], session="parent", folder="-srv")
        result = self.kitchen("retro", "--since", "1d", "--tool", "claude", cwd=self.home)
        self.assertIn("y en inglés?", self.texts("--tool", "claude"))
        self.assertIn("1 unknown provenance", result.stdout.splitlines()[-1])

    def test_t3_notices_compaction_artifacts_and_human_quoted_notice(self):
        typed = {"entrypoint": "sdk-ts", "origin": None, "promptSource": "sdk"}
        quoted = "Note: the T3 server restarted, and this background work was cancelled\nEso es falso."
        summary = "<artifact-content-authored-by-others/>\nThis session is being continued from a previous conversation."
        self.write_claude([
            self.claude_event("Update on pull request #62 (https://github.com/o/r/pull/62), which T3 Code is watching for you:\n- passed", **typed),
            self.claude_event("Note: the T3 server restarted, and this background work was cancelled before it finished.", **typed, ts="2099-01-01T10:01:00Z"),
            self.claude_event("Delegated tasks node:a, node:b reached terminal states.", **typed, ts="2099-01-01T10:01:10Z"),
            self.claude_event(summary, **typed, ts="2099-01-01T10:01:20Z"),
            self.claude_event(quoted, origin={"kind": "human"}, ts="2099-01-01T10:02:00Z"),
        ])
        self.assertEqual(self.texts("--tool", "claude"), [quoted])

    def test_codex_provenance_heartbeats_t3_sends_redaction_and_match(self):
        heartbeat = "<heartbeat>\n  <automation_id>phone-progress</automation_id>\n</heartbeat>"
        brief = "Lead update 2: read UPDATE-2.md and check the live model."
        self.write_codex("thread", "vscode", [
            "## Context:\nfiles\n## My request for Codex:\nsube a dev",
            (heartbeat, False),
            (brief, False),
            ("Delegated task node:delegated-task:abc reached a terminal state.", False),
            "use token=abc123 please",
        ])
        self.write_codex("probe", "exec", ["say hi"], originator="codex_exec")
        self.write_codex("child", "vscode", ["explore"], thread_source="subagent")
        self.write_claude([
            {"type": "assistant", "timestamp": "2099-01-01T09:59:50Z", "cwd": "/work/lead", "message": {"content": [
                {"type": "tool_use", "name": "mcp__t3-code__t3_thread_send", "input": {"threadId": "thread", "message": brief}}]}},
        ], session="lead", folder="-work-lead")

        self.assertEqual(self.texts("--tool", "codex"), ["sube a dev", "use [REDACTED] please"])
        self.assertEqual(self.texts("--tool", "codex", "--match", "sube"), ["sube a dev"])
        prompts = json.loads(self.retro("--tool", "codex", "--include-automated").stdout)
        self.assertTrue(any(p["excluded"] == "automation" for p in prompts))
        self.assertTrue(any(p["excluded"] == "agent-launched" for p in prompts))

    def test_multiple_claude_accounts_and_remote_ssh_sources(self):
        self.write_claude([self.claude_event("aquí", ts="2099-01-01T10:00:00Z")])
        second = self.home / ".claude-personal-2" / "projects" / "-work-api"
        second.mkdir(parents=True)
        (second / "s2.jsonl").write_text(json.dumps(self.claude_event("dos", ts="2099-01-01T10:30:00Z")) + "\n")

        (self.home / "config").mkdir(exist_ok=True)
        (self.home / "config" / "retro-sources.txt").write_text("ssh far-host ops\nssh dead-host\n")
        remote = [{"ts": "2099-01-01T09:00:00+00:00", "tool": "claude", "project": "/w/api", "session": "r1",
                   "text": "allá", "excluded": None, "source": "claude ~/.claude"}]
        body = (f'if [ "$5" = dead-host ] || [ "$7" = dead-host ]; then exit 255; fi\n'
                f"cat <<'EOF'\n{json.dumps(remote)}\nEOF\n"
                "echo '1 prompts included · 0 excluded (none) · 0 unknown provenance · sources: claude ~/.claude ok' >&2")
        self.fake("fake-ssh", f'printf "%s\\n" "$@" >> "{self.home}/ssh-args"\n{body}')

        result = self.kitchen("retro", "--since", "1d", "--json", cwd=self.home,
                              env_extra={"KITCHEN_SSH": str(self.fakebin / "fake-ssh")})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([p["text"] for p in json.loads(result.stdout)], ["allá", "aquí", "dos"])
        self.assertIn("ssh far-host ops ok [claude ~/.claude ok]", result.stderr)
        self.assertIn("ssh dead-host unreachable", result.stderr)

        home_only = self.kitchen("retro", "--since", "1d", "--home-only", cwd=self.home,
                                 env_extra={"KITCHEN_SSH": str(self.fakebin / "fake-ssh")})
        self.assertNotIn("far-host", home_only.stdout)


if __name__ == "__main__":
    unittest.main()
