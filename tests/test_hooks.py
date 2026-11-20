"""Tests for hooks/kitchen-guard (against tests/corpus/hooks/cases.json + config/denylist/scratch/payload edge cases)
and hooks/chef-mode-after-compact."""
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
CORPUS = json.loads((Path(__file__).resolve().parent / "corpus" / "hooks" / "cases.json").read_text(encoding="utf-8"))
GUARD = "kitchen-guard"
AFTER_COMPACT = "chef-mode-after-compact"
GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test", "GIT_COMMITTER_NAME": "t",
          "GIT_COMMITTER_EMAIL": "t@example.test"}


def clean_env(**extra):
    return {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, **extra}


def load_hook(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(HOOKS / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    sys.modules[loader.name] = module
    try:
        loader.exec_module(module)
    finally:
        del sys.modules[loader.name]
    return module


class HookFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/var/tmp")
        root = Path(self.tmp.name).resolve()
        self.home, self.tmpdir, self.config = root / "home", root / "tmp", root / "config"
        for folder in (self.home, self.tmpdir, self.config):
            folder.mkdir()
        (self.config / "shared-branches.txt").write_text("\n".join(CORPUS["defaults"]["shared_branches_file"]) + "\n")
        self.denylist = root / "denylist.txt"
        self.denylist.write_text("\n".join(CORPUS["defaults"]["denylist"]) + "\n")
        self.repos = {}

    def tearDown(self):
        self.tmp.cleanup()

    def repo(self, branch, upstream=None, push_default=None, push_config=None):
        key = (branch, upstream, push_default, push_config)
        if key not in self.repos:
            path = self.home / "projects" / f"p{len(self.repos)}"
            env = clean_env(**GIT_ID)
            for args in (["init", "-q", "-b", branch, str(path)], ["-C", str(path), "commit", "-q", "--allow-empty", "-m", "init"]):
                subprocess.run(["git", *args], check=True, env=env, capture_output=True)
            config = {"remote.origin.url": "https://example.test/r.git"}
            if upstream:
                config.update({f"branch.{branch}.remote": "origin", f"branch.{branch}.merge": f"refs/heads/{upstream}"})
            if push_default:
                config["push.default"] = push_default
            if push_config:
                config["remote.origin.push"] = push_config
            for name, value in config.items():
                subprocess.run(["git", "-C", str(path), "config", name, value], check=True, env=env)
            self.repos[key] = path
        return self.repos[key]

    def kitchen_checkout(self):
        path = self.home / "projects" / "kitchen"
        if not path.exists():
            subprocess.run(["git", "init", "-q", str(path)], check=True, env=clean_env(), capture_output=True)
            for rel in ("bin/kitchen", "lib/kitchen/common.py"):
                (path / rel).parent.mkdir(parents=True, exist_ok=True)
                (path / rel).write_text("")
        return path

    def env(self, **extra):
        return clean_env(**{"HOME": str(self.home), "TMPDIR": str(self.tmpdir), "KITCHEN_CONFIG": str(self.config),
                            "KITCHEN_DENYLIST": str(self.denylist), **extra})

    def payload(self, command, cwd):
        return {"session_id": "s", "cwd": str(cwd), "permission_mode": "default", "hook_event_name": "PreToolUse",
                "tool_name": "Bash", "tool_input": {"command": command}, "tool_use_id": "toolu_1"}

    def run_hook(self, name, payload, python=None, env=None):
        command = [python, str(HOOKS / name)] if python else [str(HOOKS / name)]
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(command, input=data, capture_output=True, text=True, env=env or self.env(), timeout=30)

    def case_cwd(self, case):
        defaults = CORPUS["defaults"]
        if case.get("cwd", defaults["cwd"]) == "tmp":
            return self.tmpdir
        if case.get("cwd") == "kitchen":
            return self.kitchen_checkout()
        return self.repo(case.get("branch", defaults["branch"]), case.get("upstream"), case.get("push_default"), case.get("push_config"))


class HookCorpusTests(HookFixture):
    def test_hooks_directory_contains_only_kitchen_guard_and_after_compact(self):
        hooks = {p.name for p in HOOKS.iterdir() if p.is_file() and "." not in p.name and os.access(p, os.X_OK)}
        self.assertEqual(hooks, {GUARD, AFTER_COMPACT})

    def test_corpus_structure_and_cases(self):
        kinds = {c["kind"] for c in CORPUS["cases"]}
        self.assertEqual(kinds, {"positive", "negative", "bypass", "known-limit"})
        for case in CORPUS["cases"]:
            expected = {"positive": "deny", "bypass": "deny", "negative": "allow", "known-limit": "allow"}[case["kind"]]
            self.assertEqual(case["expect"], expected, case["id"])
            if case["kind"] == "known-limit":
                self.assertTrue(case.get("limit"), case["id"])
            result = self.run_hook(GUARD, self.payload(case["command"], self.case_cwd(case)))
            with self.subTest(case["id"], kind=case["kind"]):
                if case["expect"] == "deny":
                    self.assertEqual(result.returncode, 2, f"{case['command']!r} was allowed")
                    self.assertIn(f"Blocked by the kitchen hook {GUARD}", result.stderr)
                    if "advice" in case:
                        self.assertIn(case["advice"], result.stderr)
                else:
                    self.assertEqual((result.returncode, result.stderr), (0, ""), f"{case['command']!r} was blocked")


class GuardEdgeTests(HookFixture):
    def test_agent_per_user_scratch_dirs_are_treated_as_temp(self):
        uid = os.getuid()
        scratch = self.home / ".cache" / "claude-tmp" / f"claude-{uid}" / "task-1"
        scratch.mkdir(parents=True)
        allowed = self.run_hook(GUARD, self.payload(f"rm -rf {scratch}", self.home))
        self.assertEqual((allowed.returncode, allowed.stderr), (0, ""))

        other_uid = self.home / ".cache" / "claude-tmp" / f"claude-{uid + 1}" / "task-1"
        other_uid.mkdir(parents=True)
        blocked = self.run_hook(GUARD, self.payload(f"rm -rf {other_uid}", self.home))
        self.assertEqual(blocked.returncode, 2)

        # CLAUDE_CODE_TMPDIR env var also counts as temp
        custom = self.home / "my-scratch"
        custom.mkdir()
        (custom / "work").mkdir()
        ok_env = self.run_hook(GUARD, self.payload(f"rm -rf {custom / 'work'}", self.home),
                               env=self.env(CLAUDE_CODE_TMPDIR=str(custom)))
        self.assertEqual((ok_env.returncode, ok_env.stderr), (0, ""))

    def test_shared_push_respects_mirror_remote_push_refspec_and_inherited_git_config(self):
        path = self.repo("feat/x")
        subprocess.run(["git", "-C", str(path), "config", "remote.origin.mirror", "true"], check=True, env=clean_env())
        res = self.run_hook(GUARD, self.payload("git push", path))
        self.assertEqual(res.returncode, 2)
        self.assertIn("remote.origin.mirror", res.stderr)

        path2 = self.repo("feat/y")
        subprocess.run(["git", "-C", str(path2), "config", "remote.fork.push", "HEAD:refs/heads/main"], check=True, env=clean_env())
        res2 = self.run_hook(GUARD, self.payload("git push", path2))
        self.assertEqual(res2.returncode, 2)
        self.assertIn("remote.fork.push", res2.stderr)

        res3 = self.run_hook(GUARD, self.payload("git push origin", self.repo("feat/z")),
                             env=self.env(GIT_CONFIG_COUNT="1"))
        self.assertEqual(res3.returncode, 2)
        self.assertIn("GIT_CONFIG_COUNT", res3.stderr)

    def test_private_publish_scans_files_handles_unreadable_denylist_and_matches_kitchen_check(self):
        kitchen = self.kitchen_checkout()
        (kitchen / "notes").mkdir()
        (kitchen / "notes" / "pr.md").write_text("Built for Acmecorp.\n")
        for cmd in ("gh pr create --title x --body-file notes/pr.md", "git commit -F notes/pr.md",
                    "cd notes && gh issue comment 3 -F pr.md", "gh pr create --title x --body-file - < notes/pr.md",
                    "cat notes/pr.md | git commit -F -", "sh -c 'git commit -F -' < notes/pr.md"):
            with self.subTest(cmd):
                res = self.run_hook(GUARD, self.payload(cmd, kitchen))
                self.assertEqual(res.returncode, 2, res.stderr)
                self.assertIn("holds `Acmecorp`", res.stderr)

        # No denylist -> allowed
        no_dl = self.run_hook(GUARD, self.payload('git commit -m "Acmecorp"', kitchen),
                              env=self.env(KITCHEN_DENYLIST=str(self.home / "none.txt")))
        self.assertEqual(no_dl.returncode, 0)

        # Unreadable denylist -> blocks in kitchen, allows elsewhere
        broken = self.home / "broken.txt"
        broken.write_bytes(b"Acme\xffcorp\n")
        env = self.env(KITCHEN_DENYLIST=str(broken))
        self.assertEqual(self.run_hook(GUARD, self.payload('git commit -m "ok"', kitchen), env=env).returncode, 2)
        self.assertEqual(self.run_hook(GUARD, self.payload('git commit -m "ok"', self.repo("feat/x")), env=env).returncode, 0)

        # Matches lib/kitchen/common.py private_terms()
        denylist = self.home / "tricky.txt"
        denylist.write_bytes("# comment\n\nAcmecorp\n  zorblat  \r\nC++ shop\nfoo.bar\n".encode())
        guard = load_hook(GUARD)
        sys.path.insert(0, str(ROOT / "lib"))
        try:
            from kitchen import common
        finally:
            sys.path.pop(0)
        with mock.patch.dict(os.environ, {"KITCHEN_DENYLIST": str(denylist)}):
            self.assertEqual([(p.pattern, p.flags) for p in guard.private_terms()],
                             [(p.pattern, p.flags) for p in common.private_terms()])

    def test_codex_argv_non_shell_and_unreadable_payloads(self):
        argv_payload = {"cwd": str(self.home), "hook_event_name": "PreToolUse", "tool_name": "Bash",
                        "tool_input": {"command": ["bash", "-lc", "git push origin dev"]}}
        self.assertEqual(self.run_hook(GUARD, argv_payload).returncode, 2)

        non_shell = {"cwd": str(self.home), "hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"patch": "x"}}
        self.assertEqual(self.run_hook(GUARD, non_shell).returncode, 0)

        for label, bad in (("not json", "{oops"), ("empty", "{}"), ("list", "[]"),
                           ("missing command", json.dumps({"tool_name": "Bash", "tool_input": {}})),
                           ("deeply nested", "[" * 100000 + "]" * 100000)):
            with self.subTest(label):
                res = self.run_hook(GUARD, bad)
                self.assertEqual(res.returncode, 2)
                self.assertIn("cannot read the hook input", res.stderr)

    @unittest.skipUnless(Path("/usr/bin/python3").exists(), "no system python3")
    def test_runs_on_system_python(self):
        for cmd in ("git commit -n -m x", "git push origin dev", "rm -rf ~"):
            res = self.run_hook(GUARD, self.payload(cmd, self.home), python="/usr/bin/python3")
            self.assertEqual(res.returncode, 2, res.stderr)


class AfterCompactTests(HookFixture):
    def test_prints_all_13_principles_and_reread_line_without_transcript_parsing(self):
        for label, extra, expected_path in (
            ("claude", {"CLAUDE_PROJECT_DIR": str(self.home)}, "~/.claude/skills/chef-mode/SKILL.md"),
            ("codex", {}, "~/.agents/skills/chef-mode/SKILL.md"),
        ):
            env = {k: v for k, v in self.env().items() if k != "CLAUDE_PROJECT_DIR"}
            env.update(extra)
            with self.subTest(label):
                res = self.run_hook(AFTER_COMPACT, "{}", env=env)
                self.assertEqual((res.returncode, res.stderr), (0, ""))
                out = json.loads(res.stdout)["hookSpecificOutput"]
                self.assertEqual(out["hookEventName"], "SessionStart")
                ctx = out["additionalContext"]
                self.assertIn(f"If you were in chef mode, re-read {expected_path} now.", ctx)
                for pid in ("prove", "truthful-state", "no-fallbacks", "root-cause", "encode-lessons",
                            "owner-attention", "doors", "evidence", "small-owned", "isolate",
                            "cross-review", "handoff", "design"):
                    self.assertIn(f"`{pid}`", ctx)


if __name__ == "__main__":
    unittest.main()
