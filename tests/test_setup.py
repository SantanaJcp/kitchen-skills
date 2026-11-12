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
        self.config = self.root / "config"  # never the real ~/.config/kitchen
        (self.config / "automation").mkdir(parents=True)
        patch = mock.patch.dict(os.environ, {"KITCHEN_CONFIG": str(self.config)})
        patch.start()
        self.addCleanup(patch.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/bin/sh\n{body}\n")
        path.chmod(0o755)

    def test_missing_agent_clis_and_srt_are_reported(self):
        config = self.config
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
        for label in ("com.kitchen.shop.nightly-guard", "com.kitchen.shop.weekly-gardener"):  # timed, as install-schedule writes them
            (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "StartCalendarInterval": {"Hour": 2}}))
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
        units.mkdir(parents=True, exist_ok=True)
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
        self.assertIn("no notification server confirmed on the kitchen timers' session bus", warnings[0])
        self.assertIn("(Call failed: The name is not activatable)", warnings[0])

    def test_the_probe_asks_the_timers_bus_for_the_server_and_shows_nothing(self):
        args = self.root / "args"
        report = self.linux_timer_report(f'printf "%s\\n" "$@" > {args}; echo \'ssss "mako" "emersion" "1.9" "1.2"\'')

        self.assertIn(("INFO", "notification server mako answers on the kitchen timers' session bus"), report)
        self.assertEqual(args.read_text().split(), ["--user", "--wait", "--pipe", "--quiet", "busctl", "--user", "call",
                                                    "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                                    "org.freedesktop.Notifications", "GetServerInformation"])

    def test_notifications_off_by_configuration_are_info_and_the_bus_is_not_probed(self):
        (self.config / "automation" / "shop.env").write_text('BRANCH=dev\nKITCHEN_NOTIFY="off"  # no desktop for this user\n')
        probed = self.root / "probed"

        report = self.linux_timer_report(f': > {probed}; echo "Call failed: The name is not activatable" >&2; exit 1')

        self.assertEqual([m for level, m in report if level == "WARN"], [], report)
        self.assertIn(("INFO", "notifications off by configuration (KITCHEN_NOTIFY=off) for shop: exception runs log one line "
                               "and record no warning"), report)
        self.assertFalse(probed.exists())

    def test_a_notify_setting_that_is_not_off_is_flagged_and_the_bus_still_probed(self):
        (self.config / "automation" / "shop.env").write_text("KITCHEN_NOTIFY=no\n")

        report = self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1')

        warnings = [m for level, m in report if level == "WARN"]
        self.assertEqual(len(warnings), 2, report)
        self.assertIn("KITCHEN_NOTIFY in shop.env is 'no': only off (or unset) is valid", warnings[0])
        self.assertIn("no notification server confirmed on the kitchen timers' session bus", warnings[1])

    def test_a_hash_inside_the_value_is_part_of_it_as_bash_reads_it(self):
        (self.config / "automation" / "shop.env").write_text("KITCHEN_NOTIFY=off#typo\n")

        report = self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1')

        warnings = [m for level, m in report if level == "WARN"]
        self.assertIn("KITCHEN_NOTIFY in shop.env is 'off#typo': only off (or unset) is valid", warnings[0])
        self.assertNotIn("notifications off by configuration", " ".join(m for _, m in report))

    def test_a_notify_setting_doctor_cannot_read_for_sure_is_unknown_not_off(self):
        for name, text in (("unset later", "KITCHEN_NOTIFY=off\nunset KITCHEN_NOTIFY\n"),
                           ("conditional", '[ -n "$DESKTOP" ] || KITCHEN_NOTIFY=off\n'),
                           ("inside a block", "if true; then\n  KITCHEN_NOTIFY=off\nfi\n"),
                           ("expanded", 'KITCHEN_NOTIFY="$MODE"\n')):
            with self.subTest(name):
                (self.config / "automation" / "shop.env").write_text(text)

                report = self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1')

                warnings = [m for level, m in report if level == "WARN"]
                self.assertIn("KITCHEN_NOTIFY in shop.env is unknown", warnings[0])
                self.assertIn("no notification server confirmed", warnings[1])  # unknown is not off: the bus is probed
                self.assertNotIn("notifications off by configuration", " ".join(m for _, m in report))

    def test_a_condition_around_an_unindented_assignment_makes_the_setting_unknown(self):
        # Bash never runs the middle line; a reader of single lines would see KITCHEN_NOTIFY=off.
        (self.config / "automation" / "shop.env").write_text("BRANCH=dev\nif false; then\nKITCHEN_NOTIFY=off\nfi\n")

        report = self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1')

        warnings = [m for level, m in report if level == "WARN"]
        self.assertIn("KITCHEN_NOTIFY in shop.env is unknown", warnings[0])
        self.assertIn("no notification server confirmed", warnings[1])  # the bus is still probed
        self.assertNotIn("notifications off by configuration", " ".join(m for _, m in report))

    def test_a_line_continuation_before_the_assignment_makes_the_setting_unknown(self):
        # Bash joins the two lines: X gets "prefixKITCHEN_NOTIFY=off" and KITCHEN_NOTIFY is never assigned.
        (self.config / "automation" / "shop.env").write_text("X=prefix\\\nKITCHEN_NOTIFY=off\n")

        report = self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1')

        warnings = [m for level, m in report if level == "WARN"]
        self.assertIn("KITCHEN_NOTIFY in shop.env is unknown", warnings[0])
        self.assertNotIn("notifications off by configuration", " ".join(m for _, m in report))

    def test_a_file_of_plain_assignments_reads_off(self):
        (self.config / "automation" / "shop.env").write_text(textwrap.dedent("""\
            # shop
            REPO_URL="git@example.invalid:owner/shop.git"
            export EXTRA_PATH="$HOME/bin"  # tools
            GARDENER_VERIFY_STEPS=("build|make build" "tests|make test")

            KITCHEN_NOTIFY=off
            """))

        report = self.linux_timer_report("exit 1")

        self.assertEqual([m for level, m in report if level == "WARN"], [], report)
        self.assertIn(("INFO", "notifications off by configuration (KITCHEN_NOTIFY=off) for shop: exception runs log one line "
                               "and record no warning"), report)

    # Every shape a real config holds (invented words): quoted and bare values, empty and one-line arrays, and arrays over
    # several lines whose quoted elements hold shell text, with comments, escaped quotes and a `)` that ends a word line.
    ARRAYS = textwrap.dedent("""\
        FLORP_URL="git@example.invalid:owner/florp.git"  # where it lives
        FLORP_BRANCH=dev
        FLORP_NAME='Florp tools'
        FLORP_EMPTY=()
        FLORP_ONE_LINE=("alpha" "beta" "gamma")
        FLORP_STEPS=(
          "build|make build && make check"
          # a comment line inside the array
          "say|echo \\"quux; frob\\" | wc -l"
          "tests|make test $WUMBLE"   # trailing comment
        )
        FLORP_WORDS=(snork blip
          zazzle KITCHEN_NOTIFYING
          wimble)
        FLORP_GUARD=(prove control)
        """)

    def assert_unknown(self, report):
        warnings = [m for level, m in report if level == "WARN"]
        self.assertIn("KITCHEN_NOTIFY in shop.env is unknown", warnings[0], report)
        self.assertIn("no notification server confirmed", warnings[1])  # unknown is not off: the bus is probed
        self.assertNotIn("notifications off by configuration", " ".join(m for _, m in report))

    def test_a_file_with_multi_line_arrays_and_a_plain_assignment_reads_off(self):
        (self.config / "automation" / "shop.env").write_text(self.ARRAYS + "KITCHEN_NOTIFY=off\n")
        probed = self.root / "probed"

        report = self.linux_timer_report(f': > {probed}; exit 1')

        self.assertEqual([m for level, m in report if level == "WARN"], [], report)
        self.assertIn(("INFO", "notifications off by configuration (KITCHEN_NOTIFY=off) for shop: exception runs log one line "
                               "and record no warning"), report)
        self.assertFalse(probed.exists())

    def test_the_setting_named_only_inside_a_multi_line_array_is_an_element_not_the_setting(self):
        # Bash makes "KITCHEN_NOTIFY=off" an element of FLORP_STEPS; the setting stays unset, so the bus is probed.
        (self.config / "automation" / "shop.env").write_text('FLORP_STEPS=(\n  "a|true"\n  KITCHEN_NOTIFY=off\n)\n')

        report = self.linux_timer_report('echo \'ssss "mako" "emersion" "1.9" "1.2"\'')

        self.assertEqual(report, [("INFO", "timer kitchen-shop-nightly-guard.timer active"),
                                  ("INFO", "notification server mako answers on the kitchen timers' session bus")])

    def test_a_multi_line_array_does_not_hide_a_condition_after_it(self):
        (self.config / "automation" / "shop.env").write_text(self.ARRAYS + "if false; then\nKITCHEN_NOTIFY=off\nfi\n")

        self.assert_unknown(self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1'))

    def test_an_array_the_reader_cannot_close_line_by_line_is_unknown(self):
        for name, text in (("never closed", "KITCHEN_NOTIFY=off\nFLORP=(\n  snork\n"),
                           ("a quote over two lines", 'KITCHEN_NOTIFY=off\nFLORP=(\n  "snork\n  blip"\n)\n'),
                           ("a command substitution", 'KITCHEN_NOTIFY=off\nFLORP=(\n  "$(frob)"\n)\n'),
                           ("a backtick", 'KITCHEN_NOTIFY=off\nFLORP=(\n  "`frob`"\n)\n'),
                           ("a semicolon outside quotes", "KITCHEN_NOTIFY=off\nFLORP=(\n  snork; blip\n)\n"),
                           ("a line continuation", "KITCHEN_NOTIFY=off\nFLORP=(\n  snork \\\n)\n"),
                           ("an expansion that assigns", 'FLORP=(\n  "${KITCHEN_NOTIFY:=off}"\n)\n')):
            with self.subTest(name):
                (self.config / "automation" / "shop.env").write_text(text)

                self.assert_unknown(self.linux_timer_report('echo "Call failed: The name is not activatable" >&2; exit 1'))

    def test_a_file_that_never_names_the_setting_leaves_it_unset_whatever_its_shape(self):
        # The documented config writes its verify steps as a multi-line array; unset still probes the bus.
        (self.config / "automation" / "shop.env").write_text('GARDENER_VERIFY_STEPS=(\n  "build|make build"\n)\n')

        report = self.linux_timer_report('echo \'ssss "mako" "emersion" "1.9" "1.2"\'')

        self.assertEqual(report, [("INFO", "timer kitchen-shop-nightly-guard.timer active"),
                                  ("INFO", "notification server mako answers on the kitchen timers' session bus")])

    def mac_schedule_report(self, env_text):
        (self.config / "automation" / "shop.env").write_text(env_text)
        agents = self.root / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        label = "com.kitchen.shop.weekly-gardener"
        (agents / f"{label}.plist").write_bytes(plistlib.dumps({"Label": label, "StartCalendarInterval": {"Hour": 6}}))
        self.fake("launchctl", f'printf "PID\\tStatus\\tLabel\\n-\\t0\\t{label}\\n"')
        probed = self.root / "probed"
        self.fake("systemd-run", f": > {probed}; exit 1")
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            report = environment().schedules("Darwin", self.root)
        self.assertFalse(probed.exists())  # the bus probe is Linux's
        return report

    def test_on_macos_notifications_off_by_configuration_are_info(self):
        report = self.mac_schedule_report("KITCHEN_NOTIFY=off\n")

        self.assertEqual(report, [("INFO", "schedule com.kitchen.shop.weekly-gardener loaded"),
                                  ("INFO", "notifications off by configuration (KITCHEN_NOTIFY=off) for shop: exception runs "
                                           "log one line and record no warning")])

    def test_on_macos_a_notify_setting_that_is_not_off_is_flagged(self):
        report = self.mac_schedule_report("KITCHEN_NOTIFY=of\n")

        warnings = [m for level, m in report if level == "WARN"]
        self.assertEqual(len(warnings), 1, report)
        self.assertIn("KITCHEN_NOTIFY in shop.env is 'of': only off (or unset) is valid", warnings[0])

    def test_a_probe_that_cannot_run_is_a_warning_not_a_pass(self):
        report = self.linux_timer_report(None)

        self.assertIn(("WARN", "notification server not checked: could not run systemd-run to ask the kitchen timers' session bus"),
                      report)

    def test_no_timers_no_probe(self):
        probed = self.root / "probed"
        self.fake("systemd-run", f": > {probed}; exit 1")

        with mock.patch.dict(os.environ, {"PATH": str(self.bin), "XDG_CONFIG_HOME": ""}):
            self.assertEqual(environment().schedules("Linux", self.root), [])
        self.assertFalse(probed.exists())

    def test_a_triggered_job_shows_as_triggered_with_its_last_run_not_as_a_missing_timer(self):
        state = self.root / "state"
        (state / "audit").mkdir(parents=True)
        (state / "audit" / "shop.jsonl").write_text(json.dumps({"ts": "2026-10-30T04:12:00+00:00", "status": "findings"}) + "\n")
        audit = ["/bin/bash", "/k/automation/bin/daily-audit", "shop"]
        units = self.root / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "kitchen-shop-daily-audit.service").write_text(f"[Service]\nExecStart={' '.join(audit)}\n")
        (units / "kitchen-retro.service").write_text("[Service]\nExecStart=/bin/bash /k/automation/bin/weekly-retro\n")
        agents = self.root / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.kitchen.shop.daily-audit.plist").write_bytes(plistlib.dumps({"Label": "com.kitchen.shop.daily-audit",
                                                                                     "ProgramArguments": audit}))
        (agents / "com.kitchen.retro.plist").write_bytes(plistlib.dumps({"Label": "com.kitchen.retro", "ProgramArguments": ["true"],
                                                                         "StartCalendarInterval": {"Hour": 7}}))
        self.fake("launchctl", 'printf "PID\\tStatus\\tLabel\\n-\\t0\\tcom.kitchen.shop.daily-audit\\n-\\t0\\tcom.kitchen.retro\\n"')
        self.fake("systemd-run", 'echo \'ssss "mako" "emersion" "1.9" "1.2"\'')
        triggered = "triggered (no timer: automation/bin/run-job starts it); last run:"
        audit_run = f"findings at 2026-10-30T04:12:00+00:00 ({state / 'audit' / 'shop.jsonl'})"

        with mock.patch.dict(os.environ, {"PATH": str(self.bin), "XDG_CONFIG_HOME": "", "KITCHEN_STATE": str(state)}):
            linux = environment().schedules("Linux", self.root)
            mac = environment().schedules("Darwin", self.root)

        self.assertEqual(linux, [("INFO", f"job kitchen-retro.service {triggered} no run logged"),
                                 ("INFO", f"job kitchen-shop-daily-audit.service {triggered} {audit_run}"),
                                 ("INFO", "notification server mako answers on the kitchen timers' session bus")])
        self.assertEqual(mac, [("INFO", "schedule com.kitchen.retro loaded"),
                               ("INFO", f"job com.kitchen.shop.daily-audit {triggered} {audit_run}")])

    def test_a_nightly_guard_without_its_timer_is_flagged(self):
        units = self.root / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        (units / "kitchen-shop-nightly-guard.service").write_text("[Service]\nExecStart=/bin/bash /k/automation/bin/nightly-guard shop\n")
        self.fake("systemd-run", 'echo \'ssss "mako" "emersion" "1.9" "1.2"\'')

        with mock.patch.dict(os.environ, {"PATH": str(self.bin), "XDG_CONFIG_HOME": ""}):
            report = environment().schedules("Linux", self.root)

        self.assertIn(("WARN", "kitchen-shop-nightly-guard.service has no timer, and the nightly guard runs only on its own: "
                               "`automation/bin/install-schedule --guard shop`"), report)

    def test_unreadable_launchctl_is_reported_not_skipped(self):
        agents = self.root / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        (agents / "com.kitchen.retro.plist").write_bytes(plistlib.dumps({"Label": "com.kitchen.retro"}))
        self.fake("launchctl", "exit 1")

        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            report = environment().schedules("Darwin", self.root)

        self.assertEqual(report, [("WARN", "cannot read `launchctl list`, so 1 kitchen schedules were not checked")])



# /proc/self/mountinfo lines as proc(5) lays them out: a root on disk, a RAM directory, a disk directory, one with no optional fields
MOUNTS = """\
22 1 259:2 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p2 rw
30 22 0:30 / /scratch-ram rw,nosuid,nodev shared:12 - tmpfs tmpfs rw,size=1024k
31 22 259:3 / /scratch-disk rw,relatime - btrfs /dev/nvme0n1p3 rw
"""


class MountinfoTests(unittest.TestCase):
    def kind(self, text, path):
        return environment().filesystem_of(text, path)

    def test_a_ram_temp_directory_is_tmpfs(self):
        self.assertEqual(self.kind(MOUNTS, "/scratch-ram/claude-1000"), "tmpfs")
        self.assertEqual(self.kind(MOUNTS, "/scratch-ram"), "tmpfs")

    def test_a_temp_directory_on_disk_is_its_disk_filesystem(self):
        self.assertEqual(self.kind(MOUNTS, "/scratch-disk/x"), "btrfs")
        self.assertEqual(self.kind(MOUNTS, "/var/tmp"), "ext4")

    def test_a_disk_mount_nested_inside_a_ram_mount_wins_for_paths_under_it_only(self):
        inner = "40 30 259:4 / /scratch-ram/x rw - xfs /dev/sdb1 rw\n"
        nested = inner + MOUNTS  # listed before its parent too, as after a remount: the longest mount point wins, not the order

        self.assertEqual(self.kind(nested, "/scratch-ram/x/agents"), "xfs")
        self.assertEqual(self.kind(nested, "/scratch-ram/xy"), "tmpfs")

    def test_a_mount_point_with_an_escaped_space(self):
        spaced = MOUNTS + "41 30 259:5 / /scratch-ram/my\\040disk rw shared:9 - ext4 /dev/sdc1 rw\n"

        self.assertEqual(self.kind(spaced, "/scratch-ram/my disk/agents"), "ext4")
        self.assertEqual(self.kind(spaced, "/scratch-ram/my\\040disk"), "tmpfs")

    def test_the_last_mount_on_one_point_is_the_one_on_top(self):
        self.assertEqual(self.kind(MOUNTS + "50 22 0:40 / /scratch-disk rw - ramfs ramfs rw\n", "/scratch-disk/x"), "ramfs")


class AgentScratchTests(unittest.TestCase):
    """doctor's RAM temp directory check, with a planted mountinfo and a temp directory that is no real path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.mountinfo = self.home / "mountinfo"
        self.mountinfo.write_text(MOUNTS)
        self.settings = self.home / ".claude" / "settings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def report(self, env=None, system="Linux", temp="/scratch-ram"):
        with mock.patch.dict(os.environ):
            os.environ.pop("CLAUDE_CODE_TMPDIR", None)
            os.environ.update(env or {})
            return environment().agent_scratch(system, self.home, mountinfo=self.mountinfo, temp=temp)

    def write_settings(self, text):
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(text)

    def test_ram_temp_and_no_setting_is_a_warning_with_the_fix(self):
        self.write_settings(json.dumps({"env": {"OTHER": "1"}}))

        [(level, line)] = self.report()

        self.assertEqual(level, "WARN")
        self.assertTrue(line.startswith("agents' scratch directories live in RAM: the temp directory /scratch-ram is tmpfs"), line)
        self.assertTrue(line.endswith(f"set env.CLAUDE_CODE_TMPDIR in {self.settings} to a directory on disk"), line)

    def test_set_in_the_settings_file_to_a_disk_path_is_ok_naming_it(self):
        self.write_settings(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": "/scratch-disk/agents"}}))

        self.assertEqual(self.report(), [("INFO", "agents' scratch directories are on disk in the sessions that read it: "
                                                  f"CLAUDE_CODE_TMPDIR=/scratch-disk/agents (from {self.settings}), "
                                                  "while the temp directory /scratch-ram is tmpfs, in RAM")])

    def test_an_empty_value_in_the_settings_file_wins_over_the_environment_and_is_a_warning(self):
        self.write_settings(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": ""}}))

        [(level, line)] = self.report({"CLAUDE_CODE_TMPDIR": "/scratch-disk/agents"})

        self.assertEqual(level, "WARN")
        self.assertTrue(line.startswith("agents' scratch directories live in RAM"), line)

    def test_a_path_that_cannot_be_resolved_is_said_not_a_crash(self):
        self.write_settings(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": "/scratch-disk/\u0000agents"}}))

        [(level, line)] = self.report()

        self.assertEqual(level, "WARN")
        self.assertIn("agents' scratch directories not checked", line)

    def test_set_in_the_environment_is_ok(self):
        [(level, line)] = self.report({"CLAUDE_CODE_TMPDIR": "/scratch-disk/agents"})

        self.assertEqual(level, "INFO")
        self.assertIn("CLAUDE_CODE_TMPDIR=/scratch-disk/agents (from this environment)", line)

    def test_set_to_a_ram_path_is_still_a_warning(self):
        self.write_settings(json.dumps({"env": {"CLAUDE_CODE_TMPDIR": "/scratch-ram/agents"}}))

        [(level, line)] = self.report({"CLAUDE_CODE_TMPDIR": "/scratch-disk/agents"})

        self.assertEqual(level, "WARN")
        self.assertIn(f"CLAUDE_CODE_TMPDIR=/scratch-ram/agents (from {self.settings}) is tmpfs", line)

    def test_an_unparsable_settings_file_is_said_not_passed(self):
        self.write_settings("{not json")

        [(level, line)] = self.report({"CLAUDE_CODE_TMPDIR": "/scratch-disk/agents"})

        self.assertEqual(level, "WARN")
        self.assertIn(f"agents' scratch directories not checked: cannot read env.CLAUDE_CODE_TMPDIR from {self.settings} (JSONDecodeError", line)

    def test_an_unreadable_mountinfo_says_the_check_could_not_run(self):
        self.mountinfo.unlink()

        [(level, line)] = self.report({"CLAUDE_CODE_TMPDIR": "/scratch-disk/agents"})

        self.assertEqual(level, "WARN")
        self.assertIn(f"RAM temp directory check could not run: cannot read {self.mountinfo}", line)

    def test_a_temp_directory_on_disk_has_nothing_to_say(self):
        self.assertEqual(self.report(temp="/scratch-disk"), [])

    def test_macos_has_nothing_to_say(self):
        self.mountinfo.unlink()

        self.assertEqual(self.report(system="Darwin"), [])

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
