"""`kitchen guards` puts kitchen-guard and chef-mode-after-compact in one repo, and `kitchen init` prints the playbook."""
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.test_kitchen import KITCHEN, KitchenFixture

ROOT = KITCHEN.parent.parent
GUARD = "kitchen-guard"
AFTER_COMPACT = "chef-mode-after-compact"
LEGACY = ("deny-no-verify", "deny-private-publish", "deny-recursive-rm", "deny-shared-push", "deny-unslotted-heavy", "shellparse.py")
PUSH = {"tool_name": "Bash", "hook_event_name": "PreToolUse", "permission_mode": "default", "tool_input": {"command": "git push origin HEAD:main"}}


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

    def run_handler(self, handler, cwd, env_extra=None, command=None, raw_payload=None):
        payload = raw_payload if raw_payload is not None else json.dumps({
            **PUSH, "cwd": str(cwd), **({"tool_input": {"command": command}} if command is not None else {})
        })
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
        return subprocess.run(["/bin/sh", "-c", handler["command"]], input=payload, capture_output=True, text=True,
                              cwd=cwd, env={**env, **(env_extra or {})})


class GuardsTests(GuardsFixture):
    def test_guards_installs_one_bash_handler_and_after_compact_for_both_tools(self):
        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in (GUARD, AFTER_COMPACT):
            copy = self.project / ".kitchen" / "hooks" / name
            self.assertEqual(copy.read_bytes(), (ROOT / "hooks" / name).read_bytes())
            self.assertTrue(os.access(copy, os.X_OK))
        for path, matcher, compact in ((self.claude, "Bash", "compact"), (self.codex, "^Bash$", "^compact$")):
            data = json.loads(path.read_text())["hooks"]
            self.assertEqual([g["matcher"] for g in data["PreToolUse"]], [matcher])
            self.assertEqual(len(data["PreToolUse"][0]["hooks"]), 1)
            self.assertEqual([g["matcher"] for g in data["SessionStart"]], [compact])
            self.assertEqual(len(data["SessionStart"][0]["hooks"]), 1)

    def test_each_tool_blocks_guarded_commands_and_prefilter_skips_python_on_other_commands(self):
        self.kitchen("guards", str(self.project))
        subdir = self.project / "src"
        subdir.mkdir()

        for path, extra in ((self.claude, {"CLAUDE_PROJECT_DIR": str(self.project)}), (self.codex, {})):
            handler, = self.handlers(path)
            for cmd in ("git push origin HEAD:main", "git commit --no-verify -m x", "rm -rf /kitchen-probe"):
                res = self.run_handler(handler, subdir, extra, cmd)
                self.assertEqual(res.returncode, 2, res.stderr)
                self.assertIn("Blocked by the kitchen hook kitchen-guard", res.stderr)
            # Malformed JSON still reaches kitchen-guard and fails closed
            bad = self.run_handler(handler, subdir, extra, raw_payload="{oops")
            self.assertEqual(bad.returncode, 2, bad.stderr)

        # Prove the POSIX-sh prefilter skips starting kitchen-guard when command mentions none of git/rm/gh
        marker = Path(self.tmp.name) / "guard-ran"
        guard_copy = self.project / ".kitchen" / "hooks" / GUARD
        guard_copy.write_text(f"#!/bin/sh\ntouch {marker}\nexit 2\n")
        guard_copy.chmod(0o755)
        handler, = self.handlers(self.claude)
        ls_res = self.run_handler(handler, self.project, {"CLAUDE_PROJECT_DIR": str(self.project)}, "ls -la")
        self.assertEqual(ls_res.returncode, 0, ls_res.stderr)
        self.assertFalse(marker.exists(), "prefilter should not invoke kitchen-guard on ls")
        git_res = self.run_handler(handler, self.project, {"CLAUDE_PROJECT_DIR": str(self.project)}, "git status")
        self.assertEqual(git_res.returncode, 2)
        self.assertTrue(marker.exists(), "prefilter must invoke kitchen-guard on git")

    def test_re_running_guards_replaces_old_five_handlers_and_removes_legacy_files(self):
        hooks_dir = self.project / ".kitchen" / "hooks"
        hooks_dir.mkdir(parents=True)
        for name in LEGACY:
            (hooks_dir / name).write_text("#!/bin/sh\nexit 0\n")
            (hooks_dir / name).chmod(0o755)
        (hooks_dir / "tenant-check").write_text("#!/bin/sh\nexit 0\n")  # project's own hook stays
        old_handlers = [{"type": "command", "command": f"sh -c 'h=\"$r/.kitchen/hooks/{n}\"; \"$h\"'", "timeout": 30} for n in LEGACY[:5]]
        theirs = {"type": "command", "command": "sh -c 'exec \"$CLAUDE_PROJECT_DIR/.kitchen/hooks/tenant-check\"'"}
        self.claude.parent.mkdir(parents=True)
        self.claude.write_text(json.dumps({"permissions": {"allow": ["Bash(ls)"]},
                                           "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [theirs, *old_handlers]}]}}))
        git = ["git", "-C", str(self.project), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "core.hooksPath=/dev/null"]
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "old guards"], check=True)

        result = self.kitchen("guards", str(self.project))

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in LEGACY:
            self.assertFalse((hooks_dir / name).exists(), f"{name} should be removed")
        self.assertTrue((hooks_dir / "tenant-check").is_file(), "project hook must stay")
        self.assertIn(theirs, self.handlers(self.claude))
        self.assertEqual(len(self.handlers(self.claude)), 2)  # theirs + kitchen-guard
        self.assertEqual(json.loads(self.claude.read_text())["permissions"], {"allow": ["Bash(ls)"]})

    def test_after_compact_handler_outputs_principles(self):
        self.kitchen("guards", str(self.project))
        for path, extra in ((self.claude, {"CLAUDE_PROJECT_DIR": str(self.project)}), (self.codex, {})):
            handler, = self.handlers(path, "SessionStart")
            res = self.run_handler(handler, self.project, extra, raw_payload="{}")
            self.assertEqual(res.returncode, 0, res.stderr)
            ctx = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertIn("If you were in chef mode, re-read", ctx)
            self.assertIn("`prove`", ctx)

    def test_missing_or_broken_copy_fails_closed_and_branch_without_settings_allows(self):
        self.kitchen("guards", str(self.project))
        handler, = self.handlers(self.claude)
        (self.project / ".kitchen" / "hooks" / GUARD).unlink()
        missing = self.run_handler(handler, self.project, {"CLAUDE_PROJECT_DIR": str(self.project)})
        self.assertEqual(missing.returncode, 2)
        self.assertIn("kitchen: guard missing", missing.stderr)

        shutil.rmtree(self.project / ".claude")
        shutil.rmtree(self.project / ".kitchen")
        old_branch = self.run_handler(handler, self.project, {"CLAUDE_PROJECT_DIR": str(self.project)})
        self.assertEqual(old_branch.returncode, 0)

    def test_guards_refuses_bad_json_symlinks_and_uncommitted_edits_and_is_idempotent(self):
        self.codex.parent.mkdir(parents=True)
        self.codex.write_text("{ not json")
        self.assertEqual(self.kitchen("guards", str(self.project)).returncode, 1)
        self.codex.unlink()

        self.kitchen("guards", str(self.project))
        first = self.claude.read_bytes()
        again = self.kitchen("guards", str(self.project))
        self.assertEqual(self.claude.read_bytes(), first)
        self.assertIn("(already current)", again.stdout)
        self.assertEqual(self.kitchen("guards", str(self.project), "--check").returncode, 0)

        (self.project / ".kitchen" / "hooks" / GUARD).write_text("#!/bin/sh\nexit 0\n")
        self.assertEqual(self.kitchen("guards", str(self.project), "--check").returncode, 1)
        refused = self.kitchen("guards", str(self.project))
        self.assertEqual(refused.returncode, 1)
        self.assertIn("uncommitted changes", refused.stderr)

    def test_this_repo_calls_hooks_directly_without_vendored_copy(self):
        self.assertFalse((ROOT / ".kitchen").exists(), "this repo should not keep a duplicate .kitchen/hooks copy")
        result = self.kitchen("guards", str(ROOT), "--check", env_extra={"KITCHEN_REPO": str(ROOT)})
        self.assertEqual(result.returncode, 0, "run `bin/kitchen guards .` in this repo\n" + result.stdout)


class InitTests(GuardsFixture):
    def setUp(self):
        super().setUp()
        playbook = self.repo / "skills" / "chef-mode" / "playbooks"
        playbook.mkdir(parents=True)
        (playbook / "init.md").write_text("# init: set one repo up for agents\n\nStep one.\n")

    def test_init_prints_playbook_without_writing_and_refuses_non_repo(self):
        result = self.kitchen("init", str(self.project))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("/chef-mode init", result.stdout)
        self.assertIn(f"Repository: {self.project.resolve()}", result.stdout)
        self.assertEqual(self.kitchen("init", str(self.home)).returncode, 1)


class RealPlaybookTests(unittest.TestCase):
    def test_the_init_playbook_links_resolve_and_chef_mode_routes_to_it(self):
        playbooks = ROOT / "skills" / "chef-mode" / "playbooks"
        text = (playbooks / "init.md").read_text(encoding="utf-8")
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            with self.subTest(link=target):
                self.assertTrue((playbooks / target).exists())
        self.assertIn("(playbooks/init.md)", (ROOT / "skills" / "chef-mode" / "SKILL.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
