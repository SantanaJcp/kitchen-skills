"""doctor's environment checks (python3, gh, nightly timers) and CLI help."""
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.test_kitchen import KITCHEN, KitchenFixture

ROOT = KITCHEN.parent.parent


def environment():
    sys.path.insert(0, str(ROOT / "lib"))
    try:
        from kitchen import environment as module
    finally:
        sys.path.pop(0)
    return module


class EnvironmentDoctorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tools_checks_python3_and_gh(self):
        env_mod = environment()
        with mock.patch.object(env_mod, "on_path", side_effect=lambda name: None):
            report = env_mod.tools()
        self.assertEqual([lvl for lvl, _ in report], ["FAIL", "WARN"])
        self.assertIn("python3", report[0][1])
        self.assertIn("gh", report[1][1])

        with mock.patch.object(env_mod, "on_path", side_effect=lambda name: f"/usr/bin/{name}"):
            self.assertEqual(env_mod.tools(), [])

    def test_launchd_reports_loaded_and_unloaded_plists(self):
        env_mod = environment()
        agents = self.home / "Library" / "LaunchAgents"
        agents.mkdir(parents=True)
        for name in ("com.kitchen.shop.nightly-guard", "com.kitchen.api.nightly-guard"):
            (agents / f"{name}.plist").write_bytes(plistlib.dumps({"Label": name}))
        listing = subprocess.CompletedProcess(["launchctl", "list"], 0, "PID\tStatus\tLabel\n-\t0\tcom.kitchen.shop.nightly-guard\n", "")
        with mock.patch.object(env_mod, "run", return_value=listing):
            report = env_mod.schedules("Darwin", self.home)
        self.assertIn(("INFO", "schedule com.kitchen.shop.nightly-guard loaded"), report)
        self.assertTrue(any(lvl == "WARN" and "com.kitchen.api.nightly-guard" in msg for lvl, msg in report))

    def test_systemd_reports_active_and_inactive_timers(self):
        env_mod = environment()
        units = self.home / ".config" / "systemd" / "user"
        units.mkdir(parents=True)
        for name in ("kitchen-shop-nightly-guard.timer", "kitchen-api-nightly-guard.timer"):
            (units / name).write_text("[Timer]\nOnCalendar=*-*-* 02:00:00\n")

        def fake_run(*args):
            timer = args[-1]
            return subprocess.CompletedProcess(list(args), 0 if "shop" in timer else 3, "active\n" if "shop" in timer else "inactive\n", "")

        with mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.home / ".config")}), \
                mock.patch.object(env_mod, "run", side_effect=fake_run):
            report = env_mod.schedules("Linux", self.home)
        self.assertIn(("INFO", "timer kitchen-shop-nightly-guard.timer active"), report)
        self.assertTrue(any(lvl == "WARN" and "kitchen-api-nightly-guard.timer" in msg for lvl, msg in report))


class HelpTests(KitchenFixture):
    def test_help_leads_with_examples_and_install_points_to_doctor(self):
        result = self.kitchen("--help")
        self.assertIn("examples:\n  kitchen install", result.stdout)
        self.assertIn("kitchen check", result.stdout)
        self.assertNotIn("kitchen slot", result.stdout)
        self.assertIn("next:      kitchen doctor", self.kitchen("install").stdout)


if __name__ == "__main__":
    unittest.main()
