"""The agent hooks in hooks/ against their attack corpus, through the real PreToolUse interface.

Each case is sent as the JSON payload Claude Code and Codex write on stdin; a guard allows with exit 0 and blocks
with exit 2 and an actionable message on stderr. HOME, TMPDIR and the kitchen config are throwaway; HOME is created
outside /tmp so "outside the temp dir" means something on Linux too.
"""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
HOOKS = ROOT / "hooks"
CORPUS = json.loads((Path(__file__).resolve().parent / "corpus" / "hooks" / "cases.json").read_text(encoding="utf-8"))
GUARDS = ("deny-no-verify", "deny-shared-push", "deny-recursive-rm", "deny-private-publish")
AFTER_COMPACT = "chef-mode-after-compact"  # blocks nothing, so it has no attack corpus: AfterCompactTests below
CHEF_MODE = ROOT / "skills" / "chef-mode" / "SKILL.md"
# Each block names its way out: what to do instead. A case with its own `advice` names a different way out.
ADVICE = {"deny-no-verify": "Run it without", "deny-shared-push": "Push your own branch", "deny-recursive-rm": "trash ",
          "deny-private-publish": "on stdin"}
GIT_ID = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.test", "GIT_COMMITTER_NAME": "t",
          "GIT_COMMITTER_EMAIL": "t@example.test"}


def clean_env(**extra):
    return {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, **extra}


class HookFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/var/tmp")
        root = Path(self.tmp.name).resolve()
        self.home, self.tmpdir, self.config = root / "home", root / "tmp", root / "config"
        for folder in (self.home, self.tmpdir, self.config):
            folder.mkdir()
        (self.config / "shared-branches.txt").write_text("\n".join(CORPUS["defaults"]["shared_branches_file"]) + "\n")
        self.denylist = root / "denylist.txt"  # invented terms; never the real ~/.config/kitchen/denylist.txt
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
        """A git repo with the files deny-private-publish recognizes the kitchen by."""
        path = self.home / "projects" / "kitchen"
        if not path.exists():
            subprocess.run(["git", "init", "-q", str(path)], check=True, env=clean_env(), capture_output=True)
            for rel in ("bin/kitchen", "lib/kitchen/common.py"):
                (path / rel).parent.mkdir(parents=True, exist_ok=True)
                (path / rel).write_text("")
        return path

    def env(self):
        return clean_env(HOME=str(self.home), TMPDIR=str(self.tmpdir), KITCHEN_CONFIG=str(self.config),
                         KITCHEN_DENYLIST=str(self.denylist))

    def run_guard(self, guard, payload, python=None):
        command = [python, str(HOOKS / guard)] if python else [str(HOOKS / guard)]
        data = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(command, input=data, capture_output=True, text=True, env=self.env(), timeout=30)

    def payload(self, command, cwd):
        return {"session_id": "s", "transcript_path": str(self.home / "t.jsonl"), "cwd": str(cwd), "permission_mode": "default",
                "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command, "description": "x"},
                "tool_use_id": "toolu_1"}

    def case_cwd(self, case):
        defaults = CORPUS["defaults"]
        if case.get("cwd", defaults["cwd"]) == "tmp":
            return self.tmpdir
        if case.get("cwd") == "kitchen":
            return self.kitchen_checkout()
        return self.repo(case.get("branch", defaults["branch"]), case.get("upstream"), case.get("push_default"), case.get("push_config"))


class HookCorpusTests(HookFixture):
    def test_every_guard_has_positive_negative_and_bypass_cases(self):
        for guard in GUARDS:
            kinds = {case["kind"] for case in CORPUS["cases"] if case["guard"] == guard}
            self.assertLessEqual({"positive", "negative", "bypass"}, kinds, guard)
            self.assertTrue((HOOKS / guard).is_file() and os.access(HOOKS / guard, os.X_OK), guard)
        for case in CORPUS["cases"]:
            expected = {"positive": "deny", "bypass": "deny", "negative": "allow", "known-limit": "allow"}[case["kind"]]
            self.assertEqual(case["expect"], expected, case["id"])
            if case["kind"] == "known-limit":
                self.assertTrue(case.get("limit"), f"{case['id']}: a known limit says why the guard misses it")

    def test_every_hook_is_a_guard_in_the_corpus_or_the_after_compact_hook(self):
        hooks = {p.name for p in HOOKS.iterdir() if p.is_file() and "." not in p.name and os.access(p, os.X_OK)}
        self.assertEqual(hooks, {*GUARDS, AFTER_COMPACT}, "a new guard goes in GUARDS and gets corpus cases")

    def test_corpus(self):
        for case in CORPUS["cases"]:
            payload = self.payload(case.get("command"), self.case_cwd(case))
            if "tool_name" in case:  # a tool that is not the shell
                payload.update(tool_name=case["tool_name"], tool_input=case["tool_input"])
            result = self.run_guard(case["guard"], payload)
            what = case.get("command", case.get("tool_name"))
            with self.subTest(case["id"], guard=case["guard"], kind=case["kind"]):
                if case["expect"] == "deny":
                    self.assertEqual(result.returncode, 2, f"{what!r} was allowed")
                    self.assertIn(f"Blocked by the kitchen hook {case['guard']}", result.stderr)
                    if "unterminated" not in case["id"]:
                        self.assertIn(case.get("advice", ADVICE[case["guard"]]), result.stderr)
                else:
                    self.assertEqual((result.returncode, result.stderr), (0, ""), f"{what!r} was blocked")


class SharedPushConfigTests(HookFixture):
    """Config the corpus cannot express: where a push without a refspec goes besides remote.origin.push."""

    def configure(self, *pairs):
        path = self.repo("feat/x")
        for name, value in pairs:
            subprocess.run(["git", "-C", str(path), "config", name, value], check=True, env=clean_env())
        return path

    def test_mirror_remote_is_denied(self):
        path = self.configure(("remote.origin.mirror", "true"))
        result = self.run_guard("deny-shared-push", self.payload("git push", path))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("remote.origin.mirror", result.stderr)

    def deny(self, path, command, expect, **env):
        payload = json.dumps(self.payload(command, path))
        result = subprocess.run([str(HOOKS / "deny-shared-push")], input=payload, capture_output=True, text=True,
                                env={**self.env(), **env}, timeout=30)
        self.assertEqual(result.returncode, 2, f"{command!r} was allowed")
        self.assertIn(expect, result.stderr)

    def test_configured_tag_is_a_branch_not_the_tag_shorthand(self):
        path = self.configure(("remote.origin.push", "tag"))
        subprocess.run(["git", "-C", str(path), "config", "--add", "remote.origin.push", "main"], check=True, env=clean_env())
        self.deny(path, "git push origin", "`main`")

    def test_every_remote_is_read_not_only_origin(self):
        # A sole non-origin remote and --repo both make git pick `fork`.
        path = self.configure(("remote.fork.url", "https://example.test/fork.git"), ("remote.fork.push", "HEAD:refs/heads/main"))
        self.deny(path, "git push", "remote.fork.push")
        self.deny(path, "git push --repo=fork", "remote.fork.push")

    def test_abbreviated_destination_is_resolved(self):
        path = self.configure(("remote.origin.push", "HEAD:heads/main"))
        self.deny(path, "git push origin", "heads/main")

    def test_inherited_config_is_not_trusted(self):
        path = self.repo("feat/x")
        self.deny(path, "git push origin", "GIT_CONFIG_COUNT",
                  GIT_CONFIG_COUNT="1", GIT_CONFIG_KEY_0="remote.origin.push", GIT_CONFIG_VALUE_0="HEAD:refs/heads/main")

    def test_push_remote_refspec_is_read(self):
        path = self.configure(("remote.fork.url", "https://example.test/fork.git"), ("branch.feat/x.pushRemote", "fork"),
                              ("remote.fork.push", "HEAD:refs/heads/main"))
        result = self.run_guard("deny-shared-push", self.payload("git push", path))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("remote.fork.push", result.stderr)


def load_hook(name):
    """A hook as a module, for the one test that compares its code with lib/: hooks have no .py suffix."""
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(HOOKS / name))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    path = list(sys.path)
    sys.modules[loader.name] = module
    try:
        loader.exec_module(module)
    finally:
        sys.path[:] = path
        del sys.modules[loader.name]
    return module


class PrivatePublishTests(HookFixture):
    """Where deny-private-publish reads the denylist from, what a denylist it cannot read does, and that it matches as
    `kitchen check` does. The corpus covers the commands."""

    COMMIT = 'git commit -m "Ship the Acmecorp export"'

    def guard(self, command, cwd, env=None):
        payload = json.dumps(self.payload(command, cwd))
        return subprocess.run([str(HOOKS / "deny-private-publish")], input=payload, capture_output=True, text=True,
                              env=env or self.env(), timeout=30)

    def test_no_denylist_means_no_scan(self):
        result = self.guard(self.COMMIT, self.kitchen_checkout(), {**self.env(), "KITCHEN_DENYLIST": str(self.home / "none.txt")})
        self.assertEqual((result.returncode, result.stderr), (0, ""))

    def test_without_KITCHEN_DENYLIST_it_reads_the_one_in_home(self):
        default = self.home / ".config" / "kitchen" / "denylist.txt"
        default.parent.mkdir(parents=True)
        default.write_text("Acmecorp\n")
        result = self.guard(self.COMMIT, self.kitchen_checkout(), {k: v for k, v in self.env().items() if k != "KITCHEN_DENYLIST"})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("holds `Acmecorp`, from the private denylist", result.stderr)

    def test_a_denylist_that_cannot_be_read_blocks_in_the_kitchen_only(self):
        broken = self.home / "broken.txt"
        broken.write_bytes(b"Acme\xffcorp\n")
        cases = {"not UTF-8": broken}
        if os.geteuid() != 0:  # root reads a file whatever its mode
            locked = self.home / "locked.txt"
            locked.write_text("Acmecorp\n")
            locked.chmod(0)
            cases["no read permission"] = locked
        for label, path in cases.items():
            env = {**self.env(), "KITCHEN_DENYLIST": str(path)}
            with self.subTest(label):
                result = self.guard('git commit -m "fine"', self.kitchen_checkout(), env)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("cannot read the private denylist", result.stderr)
                elsewhere = self.guard('git commit -m "fine"', self.repo("feat/x"), env)
                self.assertEqual((elsewhere.returncode, elsewhere.stderr), (0, ""))

    def test_a_body_file_it_can_read_is_scanned(self):
        kitchen = self.kitchen_checkout()
        (kitchen / "notes").mkdir()
        (kitchen / "notes" / "pr.md").write_text("Built for Acmecorp.\n")
        for command in ("gh pr create --title x --body-file notes/pr.md", "git commit -F notes/pr.md",
                        "cd notes && gh issue comment 3 -F pr.md"):
            with self.subTest(command):
                result = self.guard(command, kitchen)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("holds `Acmecorp`", result.stderr)

    def test_this_checkout_is_recognized_as_the_kitchen(self):
        # the guard knows the kitchen by two of its files; renaming one would turn the scan off without a sound
        for cwd in (ROOT, ROOT / "hooks"):
            with self.subTest(cwd=cwd.name):
                result = self.guard(self.COMMIT, cwd)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("holds `Acmecorp`", result.stderr)

    def test_it_reads_the_denylist_as_kitchen_check_does(self):
        denylist = self.home / "tricky.txt"
        denylist.write_bytes("# a comment\n\n   \n  # indented, so a term\nAcmecorp\n  zorblat  \r\nC++ shop\nÑandú\nfoo.bar\n".encode())
        guard = load_hook("deny-private-publish")
        sys.path.insert(0, str(ROOT / "lib"))
        try:
            from kitchen import common
        finally:
            sys.path.pop(0)
        with mock.patch.dict(os.environ, {"KITCHEN_DENYLIST": str(denylist)}):
            mine, kitchen_check = guard.private_terms(), common.private_terms()
        self.assertEqual(len(kitchen_check), 6)
        self.assertEqual([(p.pattern, p.flags) for p in mine], [(p.pattern, p.flags) for p in kitchen_check])
        with mock.patch.dict(os.environ, {"KITCHEN_DENYLIST": str(self.home / "none.txt")}):
            self.assertEqual((guard.private_terms(), common.private_terms()), (None, None))


class HookPayloadTests(HookFixture):
    def test_codex_payload_with_an_argv_command_is_checked(self):
        payload = {"session_id": "s", "turn_id": "t", "model": "gpt", "cwd": str(self.home), "hook_event_name": "PreToolUse",
                   "permission_mode": "default", "tool_name": "Bash", "tool_input": {"command": ["bash", "-lc", "git push origin dev"]}}

        result = self.run_guard("deny-shared-push", payload)

        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("shared branch", result.stderr)

    def test_a_tool_that_is_not_a_shell_passes(self):
        payload = {"cwd": str(self.home), "hook_event_name": "PreToolUse", "tool_name": "apply_patch", "tool_input": {"patch": "x"}}

        for guard in GUARDS:
            with self.subTest(guard):
                self.assertEqual(self.run_guard(guard, payload).returncode, 0)

    def test_unreadable_input_blocks_instead_of_passing(self):
        """Claude Code and Codex both treat only exit 2 as a block; any other failure would let the command run."""
        bad = {"not json": "{oops", "bash without command": json.dumps({"tool_name": "Bash", "tool_input": {}}),
               "empty object": "{}", "no tool name": json.dumps({"tool_input": {}}), "a list": "[]",
               "non-ascii garbage": "\u00ff\u00fe", "deeply nested": "[" * 100000 + "]" * 100000,
               "command without tool name": json.dumps({"tool_input": {"command": 7}})}
        for guard in GUARDS:
            for label, data in bad.items():
                with self.subTest(guard, input=label):
                    result = self.run_guard(guard, data)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("cannot read the hook input", result.stderr)

    def test_a_guard_whose_parser_cannot_load_blocks(self):
        broken = Path(self.tmp.name) / "hooks"
        shutil.copytree(HOOKS, broken, ignore=shutil.ignore_patterns("__pycache__"))
        (broken / "shellparse.py").write_text("raise ImportError('simulated')\n")

        for guard in GUARDS:
            with self.subTest(guard):
                result = subprocess.run([str(broken / guard)], input=json.dumps(self.payload("ls", self.home)),
                                        capture_output=True, text=True, env=self.env(), timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(f"Blocked by the kitchen hook {guard}", result.stderr)

    @unittest.skipUnless(Path("/usr/bin/python3").exists(), "no system python3")
    def test_guards_run_on_the_system_python(self):
        """The hooks run on whatever python3 the agent finds; macOS ships 3.9 at /usr/bin/python3."""
        cases = {"deny-no-verify": "git commit -n -m x", "deny-shared-push": "git push origin dev", "deny-recursive-rm": "rm -rf ~",
                 "deny-private-publish": "gh pr create --body-file /tmp/pr-body.md"}
        for guard, command in cases.items():
            with self.subTest(guard):
                result = self.run_guard(guard, self.payload(command, self.home), python="/usr/bin/python3")
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(ADVICE[guard], result.stderr)
                nested = self.run_guard(guard, "[" * 100000 + "]" * 100000, python="/usr/bin/python3")
                self.assertEqual(nested.returncode, 2, nested.stderr)
                self.assertIn("cannot read the hook input", nested.stderr)


def numbered(text):
    """A file as Claude Code's Read tool returns it: each line behind its number and a tab."""
    return "".join(f"{n:>6}\t{line}\n" for n, line in enumerate(text.splitlines(), 1))


class Session:
    """A transcript in the shapes Claude Code 2.1.312 and Codex 0.165 write (copied from real sessions, trimmed)."""

    def __init__(self, cwd):
        self.cwd, self.lines = str(cwd), [{"type": "attachment", "attachment": {"type": "date", "date": "2026-10-29"}}]

    def user(self, content, **extra):
        self.lines.append({"type": "user", "isSidechain": False, "cwd": self.cwd, **extra,
                           "message": {"role": "user", "content": content}})
        return self

    def tool(self, tool_id, name, tool_input, result, **extra):
        self.lines.append({"type": "assistant", "isSidechain": False, "cwd": self.cwd, **extra, "message": {
            "role": "assistant", "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]}})
        return self.user([{"type": "tool_result", "tool_use_id": tool_id, "content": result}], **extra)

    def slash(self, folder, body):
        self.user("<command-message>chef-mode</command-message>\n<command-name>/chef-mode</command-name>\n<command-args>go</command-args>")
        return self.user([{"type": "text", "text": f"Base directory for this skill: {folder}\n\n{body}"}], isMeta=True)

    def compacted(self):
        self.lines.append({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"})
        return self.user("Summary:\n1. Primary Request and Intent: the owner asked for X in chef mode.", isCompactSummary=True)

    def codex(self, kind, payload):
        self.lines.append({"timestamp": "2026-10-29T21:35:20.926Z", "type": kind, "payload": payload})
        return self

    def write(self, path):
        path.write_text("".join(json.dumps(line) + "\n" for line in self.lines), encoding="utf-8")
        return path


class AfterCompactTests(HookFixture):
    """chef-mode-after-compact reads the session's transcript on SessionStart `compact`, the way Claude Code and Codex
    run it: the event as JSON on stdin. Loaded: one JSON object whose additionalContext sends the agent back to SKILL.md
    with the index. Not loaded: no output. The skill is a copy of the real SKILL.md, so the markers the hook looks for
    are the ones chef-mode carries today."""

    def setUp(self):
        super().setUp()
        self.text = CHEF_MODE.read_text(encoding="utf-8")
        self.body = self.text.split("\n---\n", 1)[1].lstrip("\n")
        self.folder = self.home / ".claude" / "skills" / "chef-mode"
        self.folder.mkdir(parents=True)
        self.skill = self.folder / "SKILL.md"
        self.skill.write_text(self.text, encoding="utf-8")
        self.project = self.home / "project"
        self.project.mkdir()
        self.session = Session(self.project).user("Fix the login bug. Before any work, read the chef-mode skill's "
                                                  "SKILL.md in full (`~/.claude/skills/chef-mode/SKILL.md`) and follow it.")

    def run_hook(self, session=None, payload=None, python=None):
        transcript = (session or self.session).compacted().write(self.home / "session.jsonl")
        event = payload if payload is not None else json.dumps({
            "session_id": "s", "transcript_path": str(transcript), "cwd": str(self.project),
            "hook_event_name": "SessionStart", "source": "compact"})
        return self.run_guard(AFTER_COMPACT, event, python=python)

    def context(self, result):
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "SessionStart")
        return output["additionalContext"]

    def assert_sends_back(self, result, path):
        context = self.context(result)
        self.assertTrue(context.startswith(f"Chef mode is still on in this session. The compaction dropped it: read `{path}` "
                                           "in full before your next action."), context[:300])
        self.assertIn("\n## Principles\n\n[PRINCIPLES.md](PRINCIPLES.md) is the why behind every rule below", context)
        self.assertIn("\n- `prove`, prove it on the real thing: before you claim anything is done, fixed or safe.\n", context)
        self.assertIn("  - [experience-first](principles/experience-first.md): product, UX or scope tradeoffs.", context)
        self.assertNotIn("## Playbooks", context)
        # over 10,000 characters Claude Code moves the text to a file and shows a 2,000-character preview (hooks docs)
        self.assertLess(len(context), 10_000)
        return context

    def test_a_session_without_chef_mode_gets_nothing(self):
        session = self.session.tool("toolu_1", "Bash", {"command": "ls skills/chef-mode"}, "SKILL.md\nplaybooks\n")
        session.tool("toolu_2", "Grep", {"pattern": "chef-mode", "path": str(self.folder)}, "SKILL.md:2:name: chef-mode")
        result = self.run_hook(session)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_a_slash_command_load_is_sent_back_to_its_skill_file(self):
        self.assert_sends_back(self.run_hook(self.session.slash(self.folder, self.body)), self.skill)

    def test_a_skill_tool_load_is_sent_back_to_its_skill_file(self):
        session = self.session.tool("toolu_1", "Skill", {"skill": "chef-mode"}, "Launching skill: chef-mode")
        session.user([{"type": "text", "text": f"Base directory for this skill: {self.folder}\n\n{self.body}"}],
                     isMeta=True, sourceToolUseID="toolu_1")
        self.assert_sends_back(self.run_hook(session), self.skill)

    def test_a_read_load_is_sent_back_to_the_file_it_read(self):
        session = self.session.tool("toolu_1", "Read", {"file_path": str(self.skill)}, numbered(self.text))
        self.assert_sends_back(self.run_hook(session), self.skill)

    def test_a_shell_read_of_the_skill_is_sent_back_to_the_path_in_the_command(self):
        session = self.session.tool("toolu_1", "Bash", {"command": "cat ~/.claude/skills/chef-mode/SKILL.md; ls ~/.claude/skills",
                                                        "description": "Read chef-mode"}, self.text)
        self.assert_sends_back(self.run_hook(session), self.skill)

    def test_a_relative_path_resolves_from_the_directory_the_call_ran_in(self):
        session = Session(self.home).user("go").tool("toolu_1", "Bash", {"command": "sed -n 1,200p .claude/skills/chef-mode/SKILL.md"},
                                                     self.text)
        self.assert_sends_back(self.run_hook(session), self.skill)

    def test_the_last_skill_file_read_is_the_one_named(self):
        other = self.home / "worktree" / "skills" / "chef-mode"
        other.mkdir(parents=True)
        (other / "SKILL.md").write_text(self.text, encoding="utf-8")
        session = self.session.slash(self.folder, self.body).tool("toolu_1", "Read", {"file_path": str(other / "SKILL.md")},
                                                                  numbered(self.text))
        self.assert_sends_back(self.run_hook(session), other / "SKILL.md")

    def test_a_brief_that_only_names_the_skill_or_a_subagent_that_loads_it_is_no_load(self):
        session = self.session.tool("toolu_1", "Agent", {"prompt": "Read ~/.claude/skills/chef-mode/SKILL.md, then work."}, "done")
        session.tool("toolu_2", "Read", {"file_path": str(self.skill)}, numbered(self.text), isSidechain=True)
        result = self.run_hook(session)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_the_index_is_read_from_the_file_when_the_hook_runs(self):
        session = self.session.tool("toolu_1", "Read", {"file_path": str(self.skill)}, numbered(self.text))
        self.skill.write_text(self.text.replace("- `prove`, prove it on the real thing", "- `prove`, edited after the load"),
                              encoding="utf-8")
        self.assertIn("- `prove`, edited after the load", self.context(self.run_hook(session)))

    def test_a_skill_file_that_is_gone_is_said_loudly(self):
        session = self.session.tool("toolu_1", "Read", {"file_path": str(self.skill)}, numbered(self.text))
        self.skill.unlink()
        context = self.context(self.run_hook(session))
        self.assertTrue(context.startswith(f"Chef mode is still on in this session. The compaction dropped it, and its skill "
                                           f"file `{self.skill}` cannot be read"), context)
        self.assertIn("Tell the owner", context)

    def test_a_load_whose_path_the_transcript_does_not_name_is_said_loudly(self):
        # a Codex call with a relative path and no directory recorded for it
        session = Session(self.project).codex("response_item", {
            "type": "function_call", "call_id": "call_1", "name": "exec_command",
            "arguments": json.dumps({"cmd": "cat .claude/skills/chef-mode/SKILL.md"})})
        session.codex("response_item", {"type": "function_call_output", "call_id": "call_1", "output": self.text})
        context = self.context(self.run_hook(session))
        self.assertIn("the transcript does not say which chef-mode SKILL.md was read", context)

    def test_reading_this_hooks_source_is_no_load(self):
        source = (HOOKS / AFTER_COMPACT).read_text(encoding="utf-8")
        session = self.session.tool("toolu_1", "Bash", {"command": "cat .kitchen/hooks/chef-mode-after-compact"}, source)
        session.tool("toolu_2", "Bash", {"command": 'cat "$SKILL"'}, self.text)
        result = self.run_hook(session)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_a_prompt_that_quotes_the_load_formats_is_no_load(self):
        quoted = ("Review this test. It writes <command-message>chef-mode</command-message>\n<command-name>/chef-mode</command-name>,"
                  f"\nBase directory for this skill: {self.folder}\nand <skill>\n<name>chef-mode</name>\n<path>{self.skill}</path>")
        session = self.session.user(quoted).user([{"type": "text", "text": quoted}])
        session.codex("response_item", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": quoted}]})
        result = self.run_hook(session)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))

    def test_an_index_too_long_to_inject_is_said_instead_of_cut(self):
        long = self.text.replace("## Playbooks", "- `padding`, " + "x" * 12_000 + "\n\n## Playbooks", 1)
        self.skill.write_text(long, encoding="utf-8")
        context = self.context(self.run_hook(self.session.slash(self.folder, self.body)))
        self.assertLess(len(context), 10_000)
        self.assertIn(f"read `{self.skill}` in full before your next action", context)
        self.assertIn("too long to hand back here", context)
        self.assertNotIn("xxxx", context)

    def test_a_codex_skill_load_is_sent_back_to_its_skill_file(self):
        session = Session(self.project).codex("response_item", {"type": "message", "role": "user", "content": [
            {"type": "input_text", "text": f"<skill>\n<name>chef-mode</name>\n<path>{self.skill}</path>\n{self.text}\n</skill>"}]})
        self.assert_sends_back(self.run_hook(session), self.skill)

    def test_a_codex_shell_read_is_sent_back_to_the_path_in_the_command(self):
        session = Session(self.project).codex("event_msg", {"type": "item_completed", "item": {
            "type": "CommandExecution", "command": ["/usr/bin/bash", "-lc", "nl -ba .claude/skills/chef-mode/SKILL.md"],
            "cwd": f"file://{self.home}", "aggregated_output": numbered(self.text)}})
        self.assert_sends_back(self.run_hook(session), self.skill)

    def test_a_codex_function_call_read_is_sent_back_to_the_path_in_its_arguments(self):
        session = Session(self.project).codex("response_item", {
            "type": "function_call", "call_id": "call_1", "name": "exec_command",
            "arguments": json.dumps({"cmd": f"cat {self.skill}"})})
        session.codex("response_item", {"type": "function_call_output", "call_id": "call_1", "output": self.text})
        self.assert_sends_back(self.run_hook(session), self.skill)

    def test_unreadable_input_or_transcript_fails_with_exit_2(self):
        """On SessionStart exit 2 blocks nothing: Claude Code shows the reason to the person as a hook error."""
        missing = json.dumps({"transcript_path": str(self.home / "missing.jsonl"), "hook_event_name": "SessionStart"})
        cases = {"not json": "{oops", "a list": "[]", "no transcript": "{}", "null transcript": '{"transcript_path": null}',
                 "deeply nested": "[" * 100000 + "]" * 100000, "missing transcript": missing}
        for label, data in cases.items():
            with self.subTest(input=label):
                result = self.run_hook(payload=data)
                self.assertEqual((result.returncode, result.stdout), (2, ""))
                self.assertIn("Kitchen hook chef-mode-after-compact: cannot read the", result.stderr)

    def test_a_broken_line_fails_only_when_no_other_line_shows_a_load(self):
        transcript = self.session.compacted().write(self.home / "session.jsonl")
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "user", "message": {"content": "<command-name>/chef-mode</command-name>\n')
        event = json.dumps({"transcript_path": str(transcript), "hook_event_name": "SessionStart", "source": "compact"})
        broken = self.run_guard(AFTER_COMPACT, event)
        with transcript.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(Session(self.project).tool("toolu_1", "Read", {"file_path": str(self.skill)}, numbered(self.text)).lines[-2]) + "\n")
        loaded = self.run_guard(AFTER_COMPACT, event)
        self.assertEqual(broken.returncode, 2)
        self.assertIn("are not JSON", broken.stderr)
        self.assert_sends_back(loaded, self.skill)

    @unittest.skipUnless(Path("/usr/bin/python3").exists(), "no system python3")
    def test_it_runs_on_the_system_python(self):
        session = self.session.tool("toolu_1", "Read", {"file_path": str(self.skill)}, numbered(self.text))
        self.assert_sends_back(self.run_hook(session, python="/usr/bin/python3"), self.skill)


if __name__ == "__main__":
    unittest.main()
