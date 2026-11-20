import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

KITCHEN = Path(__file__).resolve().parent.parent / "bin" / "kitchen"

VALID_SKILL = textwrap.dedent("""\
    ---
    name: {name}
    description: "Do one thing well. Use when the fixture needs a valid skill."
    ---

    # {name}

    See [the reference](reference.md).
    """)


class KitchenFixture(unittest.TestCase):
    """A throwaway repo and HOME, so tests never touch the real setup."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.repo = root / "repo"
        self.home = root / "home"
        (self.repo / "skills").mkdir(parents=True)
        (self.repo / "bin").mkdir()
        shutil.copy(KITCHEN, self.repo / "bin" / "kitchen")
        shutil.copytree(KITCHEN.parent.parent / "lib", self.repo / "lib", ignore=shutil.ignore_patterns("__pycache__"))
        self.home.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)

    extra_env = {}

    def tearDown(self):
        self.tmp.cleanup()

    def add_skill(self, name, text=None, files=("reference.md",)):
        skill = self.repo / "skills" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(text if text is not None else VALID_SKILL.format(name=name))
        for file in files:
            (skill / file).write_text("detail\n")
        return skill

    def kitchen(self, *args, denylist=None, cwd=None, env_extra=None):
        env = {**os.environ, "HOME": str(self.home), "KITCHEN_REPO": str(self.repo), **(env_extra or {})}
        env["KITCHEN_DENYLIST"] = str(denylist or self.home / "no-denylist.txt")
        env["KITCHEN_STATE"] = str(self.home / "state")
        env["KITCHEN_CONFIG"] = str(self.home / "config")
        env.update(self.extra_env)
        return subprocess.run([sys.executable, str(KITCHEN), *args], env=env, capture_output=True, text=True, cwd=cwd)


class InstallTests(KitchenFixture):
    def test_install_links_skills_and_cli_and_writes_no_global_rules(self):
        skill = self.add_skill("alpha")

        result = self.kitchen("install")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for target in (self.home / ".claude" / "skills" / "alpha", self.home / ".agents" / "skills" / "alpha"):
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.resolve(), skill.resolve())
        cli = self.home / ".local" / "bin" / "kitchen"
        self.assertTrue(cli.is_symlink())
        self.assertEqual(cli.resolve(), (self.repo / "bin" / "kitchen").resolve())
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())
        self.assertFalse((self.home / ".codex" / "AGENTS.md").exists())

    def test_install_refuses_unmanaged_paths_and_backup_moves_them(self):
        self.add_skill("alpha")
        self.add_skill("beta")
        existing = self.home / ".agents" / "skills" / "beta"
        existing.mkdir(parents=True)
        (existing / "SKILL.md").write_text("my own beta\n")

        refused = self.kitchen("install")
        self.assertEqual(refused.returncode, 1)
        self.assertIn(str(existing), refused.stdout)
        self.assertFalse((self.home / ".claude" / "skills" / "alpha").exists())

        backed = self.kitchen("install", "--backup")
        self.assertEqual(backed.returncode, 0, backed.stdout + backed.stderr)
        self.assertTrue(existing.is_symlink())
        backups = list((self.home / ".kitchen-backups").rglob("SKILL.md"))
        self.assertEqual([b.read_text() for b in backups], ["my own beta\n"])

    def test_install_removes_deleted_skill_links_keeps_foreign_and_is_idempotent(self):
        self.add_skill("alpha")
        self.kitchen("install")
        foreign = self.home / ".claude" / "skills" / "mine"
        foreign.mkdir()

        second = self.kitchen("install")
        self.assertEqual(second.returncode, 0)
        self.assertNotIn("linked", second.stdout)

        (self.repo / "skills" / "alpha" / "SKILL.md").unlink()
        third = self.kitchen("install")
        self.assertEqual(third.returncode, 0, third.stdout + third.stderr)
        self.assertFalse((self.home / ".claude" / "skills" / "alpha").is_symlink())
        self.assertTrue(foreign.is_dir())


class DoctorTests(KitchenFixture):
    def test_doctor_fails_until_install_then_passes_and_detects_stale_or_dangling_links(self):
        skill = self.add_skill("alpha")
        before = self.kitchen("doctor")
        self.assertEqual(before.returncode, 1)
        self.assertIn("missing link", before.stdout)

        self.kitchen("install")
        (self.home / ".agents" / "skills" / "mine").mkdir(parents=True)
        after = self.kitchen("doctor")
        self.assertEqual(after.returncode, 0, after.stdout)
        self.assertIn("INFO  not managed by kitchen", after.stdout)

        (skill / "SKILL.md").rename(skill / "MOVED.md")
        stale = self.kitchen("doctor")
        self.assertEqual(stale.returncode, 1)
        self.assertIn("stale link", stale.stdout)

        shutil.rmtree(skill)
        dangling = self.kitchen("doctor")
        self.assertEqual(dangling.returncode, 1)
        self.assertIn("dangling link", dangling.stdout)


class LintTests(KitchenFixture):
    def lint(self):
        return self.kitchen("check", "--lint-only")

    def test_skill_lint_rules(self):
        self.add_skill("alpha")
        ok = self.kitchen("check", "--lint-only", "--skills-dir", str(self.repo / "skills"))
        self.assertEqual(ok.returncode, 0, ok.stdout)

        (self.repo / "skills" / "alpha" / "SKILL.md").write_text(VALID_SKILL.format(name="beta"))
        self.assertIn("must equal the directory name", self.lint().stdout)

        (self.repo / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n\n# alpha\n")
        self.assertIn("description is required", self.lint().stdout)

        (self.repo / "skills" / "alpha" / "SKILL.md").write_text("---\nname: alpha\ndescription: >\n  folded\n---\n")
        self.assertIn("block scalar", self.lint().stdout)

        (self.repo / "skills" / "alpha" / "SKILL.md").write_text(VALID_SKILL.format(name="alpha") + "line\n" * 200)
        self.assertIn("lines (max 150)", self.lint().stdout)

        (self.repo / "skills" / "alpha" / "reference.md").unlink()
        (self.repo / "skills" / "alpha" / "SKILL.md").write_text(VALID_SKILL.format(name="alpha"))
        self.assertIn("broken link reference.md", self.lint().stdout)


class InvocationParityTests(KitchenFixture):
    MANUAL = VALID_SKILL.replace("description:", "disable-model-invocation: true\ndescription:")

    def add_codex_policy(self, skill, allow):
        (skill / "agents").mkdir()
        (skill / "agents" / "openai.yaml").write_text(f"policy:\n  allow_implicit_invocation: {allow}\n")

    def test_manual_only_parity_between_claude_and_codex(self):
        skill = self.add_skill("alpha")
        self.assertIn("alpha: every kitchen skill is manual-only", self.kitchen("check", "--lint-only").stdout)

        (skill / "SKILL.md").write_text(self.MANUAL.format(name="alpha"))
        self.assertIn("manual-only for Claude but not for Codex", self.kitchen("check", "--lint-only").stdout)

        self.add_codex_policy(skill, "false")
        self.assertEqual(self.kitchen("check", "--lint-only").returncode, 0)

        (skill / "SKILL.md").write_text(VALID_SKILL.format(name="alpha"))
        self.assertIn("manual-only for Codex but not for Claude", self.kitchen("check", "--lint-only").stdout)


class RedactionTests(unittest.TestCase):
    def scrub(self, text):
        sys.path.insert(0, str(KITCHEN.parent.parent / "lib"))
        try:
            from kitchen.transcripts import scrub
        finally:
            sys.path.pop(0)
        return scrub(text)

    def test_json_keys_and_pem_blocks_are_redacted(self):
        value = "synthetic-" + "review-secret"
        text = '{"pass' + f'word": "{value}", "api' + f'Key":"{value}", "user": "ana"}}'
        scrubbed = self.scrub(text)
        self.assertNotIn(value, scrubbed)
        self.assertIn('"user": "ana"', scrubbed)

        body = "MIIEsynthetic" + "KeyBody" * 6
        block = "-----BEGIN " + "RSA PRIVATE KEY-----\n" + body + "\n-----END " + "RSA PRIVATE KEY-----"
        self.assertEqual(self.scrub(f"before\n{block}\nafter"), "before\n[REDACTED]\nafter")
        self.assertNotIn(body, self.scrub("log:\n-----BEGIN " + "PRIVATE KEY-----\n" + body + "\n"))


class CheckRunsTestsHermeticallyTests(KitchenFixture):
    def test_tests_never_inherit_git_hook_variables(self):
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "__init__.py").write_text("")
        (self.repo / "tests" / "test_env.py").write_text(textwrap.dedent("""\
            import os, unittest
            class Env(unittest.TestCase):
                def test_no_git_variables(self):
                    self.assertEqual([k for k in os.environ if k.startswith("GIT_")], [])
            """))
        hook_env = {"GIT_INDEX_FILE": str(self.repo / ".git" / "index"), "GIT_DIR": str(self.repo / ".git")}
        result = self.kitchen("check", env_extra=hook_env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class PublicSafetyTests(KitchenFixture):
    def lint(self):
        return self.kitchen("check", "--lint-only")

    def staged(self, name, content):
        path = self.repo / name
        path.write_text(content)
        subprocess.run(["git", "-C", str(self.repo), "add", "--", name], check=True)
        return path

    def test_personal_paths_denylist_staged_secrets_and_placeholders(self):
        (self.repo / "notes.md").write_text("see /" + "Users/someone/project\n")
        res = self.lint()
        self.assertEqual(res.returncode, 1)
        self.assertIn("notes.md:1: personal path", res.stdout)
        self.assertIn("WARN  private-term scan skipped", res.stdout)

        denylist = self.home / "denylist.txt"
        denylist.write_text("# clients\nacmecorp\nKestrel\nnovus\n")
        (self.repo / "notes.md").write_text("built for AcmeCorp\n")
        self.assertEqual(self.kitchen("check", "--lint-only", denylist=denylist).returncode, 1)
        (self.repo / "notes.md").write_text("a kestrel flew by a novusual word\n")
        self.assertEqual(self.kitchen("check", "--lint-only", denylist=denylist).returncode, 0)

        token = "gh" + "p_" + "A1b2C3d4" * 5
        notes = self.staged("café notes.md", f"key {token}\n")
        notes.write_text("clean now\n")
        staged_res = self.lint()
        self.assertEqual(staged_res.returncode, 1)
        self.assertIn("café notes.md (staged):1: GitHub token", staged_res.stdout)

        # Clean staged file with placeholders passes
        self.staged("café notes.md", '{"pass' + 'word": "", "tok' + 'en": "<redacted>", "secret": "${SECRET}"}\n')
        (self.repo / "notes.md").write_text("pass" + "word: <redacted>\napi_key = $API_KEY\n")
        self.assertEqual(self.lint().returncode, 0)


if __name__ == "__main__":
    unittest.main()
