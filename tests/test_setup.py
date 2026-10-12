"""install taking out the global guards older installs added, doctor's environment checks, and the path-scoped `check --fast`."""
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from tests.test_kitchen import KITCHEN, KitchenFixture

ROOT = KITCHEN.parent.parent
GUARDS = ["deny-no-verify", "deny-recursive-rm", "deny-shared-push"]


def environment():
    sys.path.insert(0, str(ROOT / "lib"))
    try:
        from kitchen import environment as module
    finally:
        sys.path.pop(0)
    return module


class LegacyHooksFixture(KitchenFixture):
    """A HOME where an older install merged the kitchen's guards into both tools' hook files."""

    def setUp(self):
        super().setUp()
        shutil.copytree(ROOT / "hooks", self.repo / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        self.claude = self.home / ".claude" / "settings.json"
        self.codex = self.home / ".codex" / "hooks.json"
        self.mine = {"type": "command", "command": "/opt/my-hook.sh"}

    def old_handlers(self):
        return [{"type": "command", "command": str(self.repo.resolve() / "hooks" / name), "timeout": 30} for name in GUARDS]

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))


class LegacyHookTests(LegacyHooksFixture):
    def test_install_removes_the_old_guards_and_keeps_the_users_settings_and_hooks(self):
        settings = {"model": "opus", "permissions": {"allow": ["Bash(ls)"]},
                    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [self.mine]},
                                             {"matcher": "Bash", "hooks": self.old_handlers()}],
                              "PostToolUse": [{"matcher": "Edit", "hooks": [self.mine]}]}}
        self.write(self.claude, settings)
        self.write(self.codex, {"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": self.old_handlers()}]}})

        result = self.kitchen("install")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.claude.read_text()),
                         {"model": "opus", "permissions": {"allow": ["Bash(ls)"]},
                          "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [self.mine]}],
                                    "PostToolUse": [{"matcher": "Edit", "hooks": [self.mine]}]}})
        self.assertEqual(json.loads(self.codex.read_text()), {})
        self.assertIn("removed    Codex: the kitchen's global guards", result.stdout)

    def test_install_adds_no_global_guards(self):
        result = self.kitchen("install")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(self.claude.exists())
        self.assertFalse(self.codex.exists())

    def test_install_leaves_a_hook_file_without_kitchen_handlers_byte_for_byte(self):
        self.write(self.claude, {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [self.mine]}]}})
        before = self.claude.read_bytes()

        result = self.kitchen("install")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.claude.read_bytes(), before)

    def test_install_refuses_a_hook_file_it_cannot_parse(self):
        self.add_skill("alpha")
        self.codex.parent.mkdir(parents=True)
        self.codex.write_text("{ not json")

        result = self.kitchen("install")

        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{self.codex}: not valid JSON", result.stdout)
        self.assertEqual(self.codex.read_text(), "{ not json")
        self.assertFalse((self.home / ".claude" / "skills" / "alpha").exists())

    def test_install_backup_moves_an_unparseable_hook_file_aside(self):
        self.codex.parent.mkdir(parents=True)
        self.codex.write_text("{ not json")

        result = self.kitchen("install", "--backup")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([b.read_text() for b in (self.home / ".kitchen-backups").rglob("hooks.json")], ["{ not json"])
        self.assertFalse(self.codex.exists())

    def test_install_removes_the_old_guards_an_install_from_another_clone_added(self):
        other = Path(self.tmp.name) / "old-clone" / "hooks"  # the old install ran from a clone that is gone now
        handlers = [{"type": "command", "command": str(other / name), "timeout": 30} for name in GUARDS]
        self.write(self.codex, {"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": handlers + [self.mine]}]}})

        before = self.kitchen("doctor")
        result = self.kitchen("install")

        self.assertEqual(before.returncode, 1, before.stdout)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(self.codex.read_text()), {"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": [self.mine]}]}})

    def test_doctor_fails_while_the_old_guards_are_installed(self):
        self.write(self.codex, {"hooks": {"PreToolUse": [{"matcher": "^Bash$", "hooks": self.old_handlers()}]}})

        before = self.kitchen("doctor")
        self.kitchen("install")
        after = self.kitchen("doctor")

        self.assertEqual(before.returncode, 1)
        self.assertIn("still runs the kitchen's global guards", before.stdout)
        self.assertEqual(after.returncode, 0, after.stdout)


class EnvironmentDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def test_missing_agent_clis_and_srt_are_reported(self):
        config = self.root / "config"
        (config / "automation").mkdir(parents=True)
        (config / "automation" / "shop.env").write_text("BRANCH=dev\n")

        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            report = environment().tools(self.root / "repo", config)

        lines = [f"{level}  {message}" for level, message in report]
        self.assertIn("WARN  `claude` is not on PATH: Codex sessions run second-opinion reviewers with it", lines)
        self.assertIn("WARN  `codex` is not on PATH: Claude Code sessions run second-opinion reviewers with it", lines)
        self.assertIn("WARN  srt is not installed, and the automation needs it: npm ci --ignore-scripts --prefix automation "
                      "(automation configured for: shop)", lines)

    def test_a_launchagent_present_but_not_loaded_is_flagged(self):
        agents = self.root / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        for label in ("com.kitchen.shop.nightly-guard", "com.kitchen.shop.weekly-gardener"):
            (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label}))
        self.fake("launchctl", 'printf "PID\\tStatus\\tLabel\\n-\\t0\\tcom.kitchen.shop.nightly-guard\\n-\\t0\\tcom.apple.x\\n"')

        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            report = environment().schedules("Darwin", self.root)

        self.assertIn(("INFO", "schedule com.kitchen.shop.nightly-guard loaded"), report)
        warnings = [m for level, m in report if level == "WARN"]
        self.assertEqual(len(warnings), 1, report)
        self.assertIn("com.kitchen.shop.weekly-gardener.plist is present but not loaded by launchctl", warnings[0])

    def test_a_plist_without_a_label_is_reported_not_guessed_from_its_name(self):
        agents = self.root / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.kitchen.shop.nightly-guard.plist").write_bytes(plistlib.dumps({"ProgramArguments": ["true"]}))
        self.fake("launchctl", 'printf "PID\\tStatus\\tLabel\\n-\\t0\\tcom.kitchen.shop.nightly-guard\\n"')

        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            report = environment().schedules("Darwin", self.root)

        self.assertEqual(report, [("WARN", f"{agents / 'com.kitchen.shop.nightly-guard.plist'} has no Label, so launchd cannot load it")])

    def test_a_systemd_timer_present_but_inactive_is_flagged(self):
        units = self.root / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        for name in ("kitchen-shop-nightly-guard.timer", "kitchen-shop-weekly-gardener.timer"):
            (units / name).write_text("[Timer]\n")
        self.fake("systemctl", '[ "$3" = kitchen-shop-nightly-guard.timer ] && { echo active; exit 0; }; echo inactive; exit 3')
        self.fake("systemd-run", 'echo \'ssss "mako" "emersion" "1.9" "1.2"\'')

        with mock.patch.dict(os.environ, {"PATH": str(self.bin), "XDG_CONFIG_HOME": ""}):
            report = environment().schedules("Linux", self.root)

        self.assertIn(("INFO", "timer kitchen-shop-nightly-guard.timer active"), report)
        warnings = [m for level, m in report if level == "WARN"]
        self.assertEqual(len(warnings), 1, report)
        self.assertIn("kitchen-shop-weekly-gardener.timer is present but inactive", warnings[0])

    def linux_timer_report(self, systemd_run):
        units = self.root / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "kitchen-shop-nightly-guard.timer").write_text("[Timer]\n")
        self.fake("systemctl", "echo active")
        if systemd_run is not None:
            self.fake("systemd-run", systemd_run)
        with mock.patch.dict(os.environ, {"PATH": str(self.bin), "XDG_CONFIG_HOME": ""}):
            return environment().schedules("Linux", self.root)

    def test_timers_without_a_notification_server_on_their_bus_are_flagged(self):
        # The text a busctl call prints when no client owns or can activate the name, measured on a host where the desktop
        # session belongs to another user than the one running the timers.
        report = self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1')

        warnings = [m for level, m in report if level == "WARN"]
        self.assertEqual(len(warnings), 1, report)
        self.assertIn("the kitchen timers cannot show notifications", warnings[0])
        self.assertIn("(Call failed: The name is not activatable)", warnings[0])

    def test_the_probe_asks_the_timers_bus_for_the_server_and_shows_nothing(self):
        args = self.root / "args"
        report = self.linux_timer_report(f'printf "%s\\n" "$@" > {args}; echo \'ssss "mako" "emersion" "1.9" "1.2"\'')

        self.assertIn(("INFO", "notification server mako answers on the kitchen timers' session bus"), report)
        self.assertEqual(args.read_text().split(), ["--user", "--wait", "--pipe", "--quiet", "busctl", "--user", "call",
                                                    "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                                    "org.freedesktop.Notifications", "GetServerInformation"])

    def test_a_probe_that_cannot_run_is_a_warning_not_a_pass(self):
        report = self.linux_timer_report(None)

        self.assertIn(("WARN", "the kitchen timers cannot show notifications: no notification server answers on this user's "
                               "session bus (could not run systemd-run); runs record `not notified` instead "
                               "(automation/README.md, Notifications)"), report)

    def test_no_timers_no_probe(self):
        self.fake("systemd-run", "exit 1")

        with mock.patch.dict(os.environ, {"PATH": str(self.bin), "XDG_CONFIG_HOME": ""}):
            self.assertEqual(environment().schedules("Linux", self.root), [])

    def test_unreadable_launchctl_is_reported_not_skipped(self):
        agents = self.root / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.kitchen.retro.plist").write_bytes(plistlib.dumps({"Label": "com.kitchen.retro"}))
        self.fake("launchctl", "exit 1")

        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            report = environment().schedules("Darwin", self.root)

        self.assertEqual(report, [("WARN", "cannot read `launchctl list`, so 1 kitchen schedules were not checked")])


class FastCheckTests(KitchenFixture):
    """`check --fast` runs the test modules the staged paths need; plain `check` runs all of them."""

    MODULE = textwrap.dedent("""\
        import os
        import unittest
        from pathlib import Path


        class Ran(unittest.TestCase):
            def test_mark(self):
                Path(os.environ["MARKS"], "{name}").touch()
        """)

    def setUp(self):
        super().setUp()
        self.marks = self.home / "marks"
        self.marks.mkdir()
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "__init__.py").write_text("")
        for name in ("test_automation", "test_kitchen"):
            (self.repo / "tests" / f"{name}.py").write_text(self.MODULE.format(name=name))
        srt = self.repo / "automation" / "node_modules" / ".bin" / "srt"
        srt.parent.mkdir(parents=True)
        srt.write_text("#!/bin/sh\necho 0.0.78\n")
        srt.chmod(0o755)

    def stage(self, *paths):
        for path in paths:
            (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
            (self.repo / path).write_text("change\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "--", *paths], check=True)

    def check(self, *args):
        result = self.kitchen("check", *args, env_extra={"MARKS": str(self.marks)})
        return result, sorted(p.name for p in self.marks.iterdir())

    def test_a_docs_only_commit_runs_no_tests(self):
        self.stage("README.md", "global/AGENTS.md")

        result, ran = self.check("--fast")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ran, [])
        self.assertIn("SKIP  tests/test_automation.py: no staged path under automation/, lib/ or tests/test_automation.py", result.stdout)

    def test_a_docs_only_commit_still_runs_the_principles_suite(self):
        (self.repo / "tests" / "test_principles.py").write_text(self.MODULE.format(name="test_principles"))
        self.stage("PRINCIPLES.md")

        result, ran = self.check("--fast")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ran, ["test_principles"])

    def test_a_cli_change_runs_the_other_modules_but_not_automation(self):
        self.stage("bin/helper.py")

        result, ran = self.check("--fast")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ran, ["test_kitchen"])

    def test_automation_lib_and_its_tests_trigger_the_automation_suite(self):
        for path in ("automation/bin/job", "lib/kitchen/x.py", "tests/test_automation.py"):
            with self.subTest(path):
                for mark in self.marks.iterdir():
                    mark.unlink()
                subprocess.run(["git", "-C", str(self.repo), "reset", "-q"], check=True)
                if path == "tests/test_automation.py":
                    subprocess.run(["git", "-C", str(self.repo), "add", "--", path], check=True)
                else:
                    self.stage(path)

                result, ran = self.check("--fast")

                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn("test_automation", ran)

    def test_plain_check_runs_every_module_whatever_is_staged(self):
        self.stage("README.md")

        result, ran = self.check()

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(ran, ["test_automation", "test_kitchen"])

    def test_missing_srt_is_one_actionable_line_not_test_failures(self):
        shutil.rmtree(self.repo / "automation")

        result, ran = self.check()

        self.assertEqual(result.returncode, 1)
        self.assertEqual([line for line in result.stdout.splitlines() if line.startswith("FAIL")],
                         ["FAIL  srt is not installed, and the automation needs it: npm ci --ignore-scripts --prefix automation"])
        self.assertEqual(ran, [])
        self.assertEqual(result.stderr, "")


class HelpTests(KitchenFixture):
    def test_help_leads_with_examples(self):
        result = self.kitchen("--help")

        self.assertIn("examples:\n  kitchen install", result.stdout)
        self.assertIn("kitchen check --fast", result.stdout)

    def test_install_points_to_doctor_next(self):
        self.assertIn("next:      kitchen doctor", self.kitchen("install").stdout)
