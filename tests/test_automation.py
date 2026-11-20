"""Tests for the minimal nightly guard, install-schedule, notify, and supervise (no PrivateTmp required)."""
import fcntl
import json
import os
import platform
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

AUTOMATION = Path(__file__).resolve().parent.parent / "automation"
GIT_ENV = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}


def clean_env(**extra):
    return {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, **GIT_ENV, **extra}


class AutomationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.origin, self.bin, self.config, self.state = root / "origin", root / "fakebin", root / "config", root / "state"
        self.calls = root / "calls.log"
        for folder in (self.origin, self.bin, self.config / "automation"):
            folder.mkdir(parents=True)
        env = clean_env()
        for args in (["init", "-q", "-b", "dev"], ["commit", "-q", "--allow-empty", "-m", "init"]):
            subprocess.run(["git", "-C", str(self.origin), *args], check=True, env=env)
        self.fake("gh", 'echo "gh $*" >> "$CALLS"; [ "${GH_FAIL:-0}" = 1 ] && exit 1\n'
                        'case "$1 $2" in "issue list") echo "${OPEN_ISSUE:-}";; "issue create") echo "https://example.test/issues/7";; esac')
        for notifier in ("osascript", "notify-send"):
            self.fake(notifier, 'echo "notify $*" >> "$CALLS"')

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, name, body):
        path = self.bin / name
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)

    def configure(self, steps, cleanup="true", needs_docker=0, project="shop", extra=""):
        step_lines = "\n".join(f'  "{s}"' for s in steps or [])
        (self.config / "automation" / f"{project}.env").write_text(textwrap.dedent(f"""\
            REPO_URL="{self.origin}"
            GH_REPO="owner/shop"
            BRANCH="dev"
            EXTRA_PATH="{self.bin}"
            NEEDS_DOCKER={needs_docker}
            GUARD_LABEL="nightly-guard"
            CLEANUP_CMD="{cleanup}"
            """) + extra + ("" if steps is None else f"\nGUARD_STEPS=(\n{step_lines}\n)\n"))

    def job_env(self, **env):
        return {**clean_env(), "KITCHEN_CONFIG": str(self.config), "KITCHEN_STATE": str(self.state),
                "KITCHEN_REAL_GH": str(self.bin / "gh"), "CALLS": str(self.calls),
                "DOCKER_WAIT_TRIES": "1", "DOCKER_WAIT_SECONDS": "0", **env}

    def run_job(self, *args, **env):
        return subprocess.run(["bash", str(AUTOMATION / "bin" / "nightly-guard"), *args],
                              env=self.job_env(**env), capture_output=True, text=True)

    def nightly(self, project="shop"):
        return [json.loads(line) for line in (self.state / "nightly" / f"{project}.jsonl").read_text().splitlines()]

    def calls_log(self):
        return self.calls.read_text() if self.calls.exists() else ""

    def notifications(self):
        return [line for line in self.calls_log().splitlines() if line.startswith("notify ")]


class NightlyGuardTests(AutomationFixture):
    def test_red_run_stops_at_first_failure_opens_redacted_issue_and_notifies(self):
        marker = Path(self.tmp.name) / "never-ran"
        secret = "gh" + "p_" + "A1b2C3d4" * 5
        self.configure(["ok|true", f"bad|echo leak {secret}; exit 1", f"after|touch {marker}"])

        result = self.run_job("shop")

        run = self.nightly()[-1]
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual((run["status"], run["failed_step"], run["cleanup"]), ("red", "bad", "ok"))
        self.assertEqual([s["name"] for s in run["steps"]], ["ok", "bad"])
        self.assertFalse(marker.exists())
        self.assertIn("gh issue create", self.calls_log())
        issue_body = next((self.state / "automation" / "shop" / "logs").glob("guard-*-issue.md")).read_text()
        self.assertNotIn(secret, issue_body)
        self.assertIn("[REDACTED]", issue_body)
        self.assertEqual(len(self.notifications()), 1)

    def test_green_run_closes_open_issue_and_stays_silent(self):
        self.configure(["ok|true"])
        result = self.run_job("shop", OPEN_ISSUE="7")

        run = self.nightly()[-1]
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(run["status"], "green")
        self.assertIn("gh issue close 7", self.calls_log())
        self.assertEqual(self.notifications(), [])

    def test_red_run_comments_on_existing_open_issue_and_handles_gh_failure_as_warning(self):
        self.configure(["bad|false"])
        self.run_job("shop", OPEN_ISSUE="7")
        self.assertIn("gh issue comment 7", self.calls_log())

        self.run_job("shop", GH_FAIL="1")
        self.assertIn("issue report failed", self.nightly()[-1]["warnings"])

    def test_empty_or_malformed_steps_are_incomplete_never_green(self):
        for steps in ([], None, ["build|"], ["| true"], ["no-sep"]):
            with self.subTest(steps=steps):
                self.configure(steps)
                res = self.run_job("shop", GUARD_NO_REPORT="1")
                run = self.nightly()[-1]
                self.assertNotEqual(res.returncode, 0)
                self.assertEqual((run["status"], run["failed_step"]), ("incomplete", "config"))

    def test_strict_step_execution_and_step_timeout(self):
        self.configure(["pipe|false | true"])
        res = self.run_job("shop", GUARD_NO_REPORT="1")
        self.assertEqual((res.returncode, self.nightly()[-1]["status"]), (1, "red"))

        self.configure(["slow|sleep 10"], extra="STEP_TIMEOUT_SECONDS=1\n")
        res = self.run_job("shop", GUARD_NO_REPORT="1")
        run = self.nightly()[-1]
        self.assertEqual((res.returncode, run["status"], run["failed_step"]), (1, "red", "slow"))
        self.assertTrue(any("timed out" in w for w in run["warnings"]), run["warnings"])

    def test_notify_off_and_invalid_notify_setting(self):
        for notifier in ("osascript", "notify-send"):
            self.fake(notifier, 'echo "notify $*" >> "$CALLS"; exit 1')
        self.configure(["bad|false"], extra="KITCHEN_NOTIFY=off\n")
        self.run_job("shop", GUARD_NO_REPORT="1")
        self.assertEqual(self.notifications(), [])
        self.assertEqual(self.nightly()[-1]["warnings"], [])

        self.configure(["bad|false"], extra="KITCHEN_NOTIFY=invalid\n")
        self.run_job("shop", GUARD_NO_REPORT="1")
        self.assertIn("not notified: see the log", self.nightly()[-1]["warnings"])

    def test_busy_lock_skips_without_recording_and_stale_running_line_is_closed(self):
        record_path = self.state / "nightly" / "shop.jsonl"
        record_path.parent.mkdir(parents=True)
        record_path.write_text(json.dumps({"ts": "2026-01-01T00:00:00+00:00", "status": "running", "run_id": "stale-1", "warnings": []}) + "\n")

        lock_path = self.state / "automation" / "shop" / "job.lock"
        lock_path.parent.mkdir(parents=True)
        self.configure(["ok|true"], extra="LOCK_WAIT_SECONDS=0\nLOCK_POLL_SECONDS=0.1\n")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = self.run_job("shop", GUARD_NO_REPORT="1")
        finally:
            os.close(fd)
        self.assertEqual(busy.returncode, 75)
        self.assertEqual(len(self.nightly()), 1)  # busy skip wrote nothing

        ok = self.run_job("shop", GUARD_NO_REPORT="1")
        self.assertEqual(ok.returncode, 0, ok.stdout)
        entries = self.nightly()
        self.assertEqual(entries[0]["status"], "incomplete")
        self.assertEqual(entries[0]["failed_step"], "killed")
        self.assertEqual(entries[1]["status"], "green")

    def test_clone_pre_push_hook_refuses_shared_branch_and_trial_does_not_record(self):
        self.configure(["ok|true"])
        trial = self.run_job("shop", GUARD_ONLY="ok")
        self.assertEqual(trial.returncode, 0)
        self.assertFalse((self.state / "nightly" / "shop.jsonl").exists())

        clone = self.state / "automation" / "shop" / "clone"
        subprocess.run(["git", "-C", str(clone), "commit", "-q", "--allow-empty", "-m", "probe"], check=True, env=clean_env())
        push = subprocess.run(["git", "-C", str(clone), "push", "origin", "HEAD:refs/heads/dev"],
                              env=clean_env(), capture_output=True, text=True)
        self.assertNotEqual(push.returncode, 0)
        self.assertIn("refusing to push", push.stderr)


class NotifyTests(unittest.TestCase):
    def notify(self, system, found, returncode=0):
        sys.path.insert(0, str(AUTOMATION / "lib"))
        try:
            import notify as mod
        finally:
            sys.path.pop(0)
        calls = []

        def run(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, "", "it broke\n")

        with mock.patch.object(mod.platform, "system", return_value=system), \
                mock.patch.object(mod.shutil, "which", side_effect=lambda name: f"/bin/{name}" if name in found else None), \
                mock.patch.object(mod.subprocess, "run", side_effect=run):
            return mod.notify("kitchen nightly: shop red", "pass" + "word=hunter2hunter2 in the log"), calls

    def test_platforms_redaction_and_missing_or_failing_notifier(self):
        (code, _), calls = self.notify("Darwin", {"osascript"})
        self.assertEqual(code, 0)
        self.assertEqual(calls[0][-2:], ["kitchen nightly: shop red", "[REDACTED] in the log"])

        (code, _), calls = self.notify("Linux", {"notify-send"})
        self.assertEqual((code, calls), (0, [["/bin/notify-send", "kitchen nightly: shop red", "[REDACTED] in the log"]]))

        (code, text), calls = self.notify("Linux", set())
        self.assertEqual((code, calls), (2, []))
        self.assertIn("no notifier available", text)

        (code, text), _ = self.notify("Darwin", {"osascript"}, returncode=1)
        self.assertEqual(code, 1)
        self.assertIn("it broke", text)


class ScheduleTests(AutomationFixture):
    def run_install(self, system, *args, linger="yes"):
        self.fake("uname", f"echo {system}")
        self.fake("launchctl", 'echo "launchctl $*" >> "$CALLS"')
        self.fake("loginctl", f'echo "loginctl $*" >> "$CALLS"; echo {linger}')
        self.fake("systemctl", 'echo "systemctl $*" >> "$CALLS"')
        home = Path(self.tmp.name) / f"home-{system}-{linger}"
        env = {**self.job_env(), "HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
               "PATH": f"{self.bin}:{os.environ['PATH']}"}
        res = subprocess.run(["bash", str(AUTOMATION / "bin" / "install-schedule"), *args], env=env, capture_output=True, text=True)
        return res, home

    def test_install_schedule_writes_only_nightly_on_macos_and_linux(self):
        self.configure(["ok|true"])
        mac, mac_home = self.run_install("Darwin", "shop", "3")
        self.assertEqual(mac.returncode, 0, mac.stderr)
        plists = list((mac_home / "Library" / "LaunchAgents").glob("*.plist"))
        self.assertEqual([p.name for p in plists], ["com.kitchen.shop.nightly-guard.plist"])
        data = plistlib.loads(plists[0].read_bytes())
        self.assertEqual(data["ProcessType"], "Standard")
        self.assertEqual(data["StartCalendarInterval"]["Hour"], 3)

        lin, lin_home = self.run_install("Linux", "shop", "4")
        self.assertEqual(lin.returncode, 0, lin.stderr)
        units = lin_home / ".config" / "systemd" / "user"
        self.assertEqual(sorted(p.name for p in units.iterdir()),
                         ["kitchen-shop-nightly-guard.service", "kitchen-shop-nightly-guard.timer"])
        self.assertIn("OnCalendar=*-*-* 04:00:00", (units / "kitchen-shop-nightly-guard.timer").read_text())

        no_linger, _ = self.run_install("Linux", "shop", linger="no")
        self.assertEqual(no_linger.returncode, 2)
        self.assertIn("loginctl enable-linger", no_linger.stderr)


class SuperviseStopGroupTests(unittest.TestCase):
    def test_a_group_that_refuses_signal_does_not_crash_supervisor(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("supervise", AUTOMATION / "lib" / "supervise.py")
        supervise = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(supervise)
        supervise.DRAIN = 0
        sent = []

        def killpg(pgid, sig):
            sent.append(sig)
            if sig == 0:
                if len([s for s in sent if s != 0]) >= 2:
                    raise ProcessLookupError
                return
            raise PermissionError(1, "Operation not permitted")

        original = supervise.os.killpg
        supervise.os.killpg = killpg
        try:
            supervise.stop_group(4242, 0.01)
        finally:
            supervise.os.killpg = original
        self.assertIn(signal.SIGTERM, sent)
        self.assertIn(signal.SIGKILL, sent)


if __name__ == "__main__":
    unittest.main()
