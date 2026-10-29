"""`kitchen guards` puts the guards in one repo, and `kitchen init` hands the setup to the person's agent."""
import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_hooks import Session, numbered
from tests.test_kitchen import KITCHEN, KitchenFixture

ROOT = KITCHEN.parent.parent
GUARDS = ["deny-no-verify", "deny-recursive-rm", "deny-shared-push"]
AFTER_COMPACT = "chef-mode-after-compact"
PUSH = {"tool_name": "Bash", "hook_event_name": "PreToolUse", "tool_input": {"command": "git push origin HEAD:main"}}
PROBES = {"deny-shared-push": "git push origin HEAD:main", "deny-no-verify": "git commit --no-verify -m x",
          "deny-recursive-rm": "rm -rf /kitchen-probe"}


class GuardsFixture(KitchenFixture):
    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / "hooks", self.repo / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        self.project = Path(self.tmp.name) / "project"
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        self.claude = self.project / ".claude" / "settings.json"
        self.codex = self.project / ".codex" / "hooks.json"

    def handlers(self, path, event="PreToolUse"):
        return [h for g in json.loads(path.read_text())["hooks"][event] for h in g["hooks"]]

    def run_handler(self, handler, cwd, env_extra=None, command=None):
        payload = json.dumps({**PUSH, "cwd": str(cwd), **({"tool_input": {"command": command}} if command else {})})
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        return subprocess.run(["/bin/sh", "-c", handler["command"]], input=payload, capture_output=True, text=True,
                              cwd=cwd, env={**env, **(env_extra or {})})


class GuardsTests(GuardsFixture):
    def test_guards_copies_the_guards_and_runs_each_from_both_tools(self):
        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in GUARDS:
            copy = self.project / ".kitchen" / "hooks" / name
            self.assertEqual(copy.read_bytes(), (ROOT / "hooks" / name).read_bytes())
            self.assertTrue(os.access(copy, os.X_OK))
        self.assertTrue((self.project / ".kitchen" / "hooks" / "shellparse.py").is_file())
        for path, matcher in ((self.claude, "Bash"), (self.codex, "^Bash$")):
            groups = json.loads(path.read_text())["hooks"]["PreToolUse"]
            self.assertEqual([g["matcher"] for g in groups], [matcher])
            self.assertEqual(len(groups[0]["hooks"]), len(GUARDS))

    def test_each_tool_blocks_a_push_to_main_in_the_repo(self):
        self.kitchen("guards", str(self.project))
        subdir = self.project / "src"
        subdir.mkdir()

        claude = [self.run_handler(h, subdir, {"CLAUDE_PROJECT_DIR": str(self.project)}) for h in self.handlers(self.claude)]
        codex = [self.run_handler(h, subdir) for h in self.handlers(self.codex)]  # Codex: no project variable, a subdirectory cwd

        for results in (claude, codex):
            self.assertEqual(sorted(r.returncode for r in results), [0, 0, 2], [r.stderr for r in results])
            self.assertTrue(any("Blocked by the kitchen hook" in r.stderr for r in results))

    def test_each_guard_blocks_its_own_probe_through_its_handler_in_both_tools(self):
        self.kitchen("guards", str(self.project))

        for path, env in ((self.claude, {"CLAUDE_PROJECT_DIR": str(self.project)}), (self.codex, {})):
            for handler in self.handlers(path):
                name = next(n for n in GUARDS if f"/.kitchen/hooks/{n}" in handler["command"])
                with self.subTest(tool=path.name, guard=name):
                    result = self.run_handler(handler, self.project, env, PROBES[name])
                    self.assertEqual(result.returncode, 2, result.stderr)

    def test_a_copy_that_cannot_run_blocks_instead_of_passing(self):
        self.kitchen("guards", str(self.project))
        copy = self.project / ".kitchen" / "hooks" / "deny-shared-push"
        copy.write_text("#!/nonexistent/interpreter\n")
        handler = next(h for h in self.handlers(self.codex) if "deny-shared-push" in h["command"])

        result = self.run_handler(handler, self.project)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("failed with exit", result.stderr)

    def test_guards_keeps_a_project_hook_of_its_own_under_kitchen_hooks(self):
        theirs = {"type": "command", "command": "sh -c 'exec \"$CLAUDE_PROJECT_DIR/.kitchen/hooks/tenant-check\"'"}
        self.claude.parent.mkdir(parents=True)
        self.claude.write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [theirs]}]}}))

        self.kitchen("guards", str(self.project))

        self.assertIn(theirs, self.handlers(self.claude))

    def test_guards_writes_through_no_planted_temporary_link(self):
        hooks = self.project / ".kitchen" / "hooks"
        hooks.mkdir(parents=True)
        victim = Path(self.tmp.name) / "victim"
        victim.write_text("KEEP")
        (hooks / ".deny-no-verify.kitchen-tmp").symlink_to(victim)

        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(victim.read_text(), "KEEP")

    def test_a_handler_fails_closed_when_its_copy_is_missing(self):
        self.kitchen("guards", str(self.project))
        (self.project / ".kitchen" / "hooks" / "deny-shared-push").unlink()

        results = [self.run_handler(h, self.project) for h in self.handlers(self.codex)]

        self.assertIn("kitchen: guard missing", "".join(r.stderr for r in results))
        self.assertEqual(sorted(r.returncode for r in results), [0, 0, 2])

    def test_a_handler_lets_everything_through_on_a_branch_without_the_settings(self):
        self.kitchen("guards", str(self.project))
        handler = self.handlers(self.claude)[2]
        shutil.rmtree(self.project / ".claude")
        shutil.rmtree(self.project / ".kitchen")

        result = self.run_handler(handler, self.project, {"CLAUDE_PROJECT_DIR": str(self.project)})

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_guards_runs_the_after_compact_hook_on_each_tools_compaction_event(self):
        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        copy = self.project / ".kitchen" / "hooks" / AFTER_COMPACT
        self.assertEqual(copy.read_bytes(), (ROOT / "hooks" / AFTER_COMPACT).read_bytes())
        self.assertTrue(os.access(copy, os.X_OK))
        for path, matcher in ((self.claude, "compact"), (self.codex, "^compact$")):
            with self.subTest(tool=path.name):
                groups = json.loads(path.read_text())["hooks"]["SessionStart"]
                self.assertEqual([g["matcher"] for g in groups], [matcher])
                self.assertEqual([h["command"].count(f"/.kitchen/hooks/{AFTER_COMPACT}") for h in groups[0]["hooks"]], [1])
                self.assertFalse(any(AFTER_COMPACT in h["command"] for h in self.handlers(path)), "it guards no Bash call")

    def test_the_installed_after_compact_handler_sends_a_chef_mode_session_back_to_its_skill(self):
        self.kitchen("guards", str(self.project))
        skill = self.home / ".claude" / "skills" / "chef-mode" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text((ROOT / "skills" / "chef-mode" / "SKILL.md").read_text(encoding="utf-8"), encoding="utf-8")
        loaded = Session(self.project).tool("toolu_1", "Read", {"file_path": str(skill)}, numbered(skill.read_text(encoding="utf-8")))
        plain = Session(self.project).user("Fix the login bug.")
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}

        for path, extra in ((self.claude, {"CLAUDE_PROJECT_DIR": str(self.project)}), (self.codex, {})):
            for label, session in (("loaded", loaded), ("plain", plain)):
                with self.subTest(tool=path.name, session=label):
                    event = json.dumps({"hook_event_name": "SessionStart", "source": "compact", "cwd": str(self.project),
                                        "transcript_path": str(session.write(self.home / f"{label}.jsonl"))})
                    handler, = self.handlers(path, "SessionStart")
                    result = subprocess.run(["/bin/sh", "-c", handler["command"]], input=event, capture_output=True,
                                            text=True, cwd=self.project, env={**env, "HOME": str(self.home), **extra})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    if label == "plain":
                        self.assertEqual(result.stdout, "")
                    else:
                        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
                        self.assertTrue(context.startswith(f"Chef mode is still on in this session. The compaction dropped it: read `{skill}`"))
                        self.assertIn("\n- `prove`, prove it on the real thing", context)

    def test_guards_keeps_a_projects_own_session_start_hook_and_reruns_byte_identical(self):
        mine = {"type": "command", "command": "./scripts/session-notes.sh"}
        self.claude.parent.mkdir(parents=True)
        self.claude.write_text(json.dumps({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [mine]}]}}))
        self.kitchen("guards", str(self.project))
        first = {path: path.read_bytes() for path in (self.claude, self.codex)}

        again = self.kitchen("guards", str(self.project))

        self.assertEqual({path: path.read_bytes() for path in first}, first)
        self.assertIn("(already current)", again.stdout)
        groups = json.loads(self.claude.read_text())["hooks"]["SessionStart"]
        self.assertEqual(groups[0], {"matcher": "startup", "hooks": [mine]})
        self.assertEqual([g["matcher"] for g in groups], ["startup", "compact"])

    def test_guards_refuses_a_session_start_it_cannot_merge(self):
        self.claude.parent.mkdir(parents=True)
        self.claude.write_text(json.dumps({"hooks": {"SessionStart": {"compact": "not a list"}}}))

        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 1)
        self.assertIn("does not have the shape hooks.SessionStart[]", result.stderr)
        self.assertFalse((self.project / ".kitchen").exists())

    def test_guards_keeps_the_projects_settings_and_hooks_and_replaces_only_its_own(self):
        mine = {"type": "command", "command": "./scripts/lint-hook.sh"}
        self.claude.parent.mkdir(parents=True)
        self.claude.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]},
                                           "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [mine]}],
                                                     "Stop": [{"hooks": [mine]}]}}))
        self.kitchen("guards", str(self.project))
        first = self.claude.read_bytes()

        again = self.kitchen("guards", str(self.project))

        data = json.loads(self.claude.read_text())
        self.assertEqual(self.claude.read_bytes(), first)
        self.assertIn("(already current)", again.stdout)
        self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
        self.assertEqual(data["hooks"]["Stop"], [{"hooks": [mine]}])
        self.assertEqual(data["hooks"]["PreToolUse"][0], {"matcher": "Bash", "hooks": [mine]})
        self.assertEqual(len(self.handlers(self.claude)), 1 + len(GUARDS))

    def test_guards_refuses_a_settings_file_it_cannot_parse_and_writes_nothing(self):
        self.codex.parent.mkdir(parents=True)
        self.codex.write_text("{ not json")

        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 1)
        self.assertIn("is not valid JSON", result.stderr)
        self.assertEqual(self.codex.read_text(), "{ not json")
        self.assertFalse((self.project / ".kitchen").exists())
        self.assertFalse(self.claude.exists())

    def test_guards_refuses_a_linked_directory_that_would_carry_the_write_out_of_the_project(self):
        outside = Path(self.tmp.name) / "personal-claude"
        outside.mkdir()
        (outside / "settings.json").write_text('{"model": "opus"}')
        (self.project / ".claude").symlink_to(outside)

        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 1)
        self.assertIn(".claude is a symlink", result.stderr)
        self.assertEqual((outside / "settings.json").read_text(), '{"model": "opus"}')
        self.assertFalse((self.project / ".kitchen").exists())

    def test_guards_refuses_to_replace_an_uncommitted_edit_and_replaces_a_committed_one(self):
        self.kitchen("guards", str(self.project))
        copy = self.project / ".kitchen" / "hooks" / "deny-shared-push"
        git = ["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null"]
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "guards"], check=True)
        copy.write_text("#!/bin/sh\n# my own rule\nexit 2\n")

        refused = self.kitchen("guards", str(self.project))
        subprocess.run([*git, "commit", "-q", "-am", "my rule"], check=True)
        replaced = self.kitchen("guards", str(self.project))

        self.assertEqual(refused.returncode, 1)
        self.assertIn("uncommitted changes in .kitchen/hooks/deny-shared-push", refused.stderr)
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertEqual(copy.read_bytes(), (ROOT / "hooks" / "deny-shared-push").read_bytes())
        shown = subprocess.run([*git, "show", "HEAD:.kitchen/hooks/deny-shared-push"], capture_output=True, text=True)
        self.assertIn("# my own rule", shown.stdout)

    def test_guards_refuses_to_replace_an_edited_copy_git_never_held(self):
        self.kitchen("guards", str(self.project))
        copy = self.project / ".kitchen" / "hooks" / "deny-shared-push"
        copy.write_text("#!/bin/sh\n# my own rule\nexit 2\n")

        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 1)
        self.assertIn("uncommitted changes in .kitchen/hooks/deny-shared-push", result.stderr)
        self.assertIn("# my own rule", copy.read_text())

    def test_check_fails_until_the_guards_are_written_and_again_when_a_copy_drifts(self):
        before = self.kitchen("guards", str(self.project), "--check")
        self.kitchen("guards", str(self.project))
        current = self.kitchen("guards", str(self.project), "--check")
        (self.project / ".kitchen" / "hooks" / "deny-no-verify").write_text("#!/bin/sh\nexit 0\n")
        drifted = self.kitchen("guards", str(self.project), "--check")

        self.assertEqual((before.returncode, current.returncode, drifted.returncode), (1, 0, 1), current.stdout)
        self.assertIn("FAIL  .kitchen/hooks/deny-no-verify differs", drifted.stdout)
        self.assertFalse(before.stdout.count("wrote"))


class InitTests(GuardsFixture):
    def setUp(self):
        super().setUp()
        playbook = self.repo / "skills" / "chef-mode" / "playbooks"
        playbook.mkdir(parents=True)
        (playbook / "init.md").write_text("# init: set one repo up for agents\n\nStep one.\n")

    def test_init_prints_the_playbook_for_the_agent_and_writes_nothing(self):
        result = self.kitchen("init", str(self.project))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/chef-mode init", result.stdout)
        self.assertIn(f"Repository: {self.project.resolve()}", result.stdout)
        self.assertIn("# init: set one repo up for agents\n\nStep one.\n", result.stdout)
        status = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain", "--ignored"], capture_output=True, text=True)
        self.assertEqual(status.stdout, "")
        self.assertFalse((self.home / "config").exists())

    def test_init_refuses_a_path_that_is_not_a_repository(self):
        result = self.kitchen("init", str(self.home))

        self.assertEqual(result.returncode, 1)
        self.assertIn("is not the root of a git repository", result.stderr)


class RealPlaybookTests(unittest.TestCase):
    def test_the_init_playbook_links_resolve_and_chef_mode_routes_to_it(self):
        playbooks = ROOT / "skills" / "chef-mode" / "playbooks"
        text = (playbooks / "init.md").read_text(encoding="utf-8")
        import re
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue((playbooks / target).exists())
        self.assertIn("(playbooks/init.md)", (ROOT / "skills" / "chef-mode" / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
