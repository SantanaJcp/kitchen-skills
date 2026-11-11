"""`kitchen slot`: a per-user queue of N slots for heavy commands, and `kitchen integrate` and the init check template taking one.

Every test coordinates through files and the waiting line on stderr, never through a sleep that assumes a speed:
the machine running them may be the loaded one this command exists for. Timeouts are generous on purpose.
"""
import datetime
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.test_kitchen import KITCHEN

ROOT = KITCHEN.parent.parent
TIMEOUT = 120
ROUNDING = 0.05  # records keep seconds to one decimal


def slot_module():
    sys.path.insert(0, str(ROOT / "lib"))
    try:
        from kitchen import slot as module
    finally:
        sys.path.pop(0)
    return module


class SlotFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home, self.work = root / "home", root / "work"
        self.home.mkdir()
        self.work.mkdir()
        # an outer slot (this suite run by a slotted check) must not make every call here a nested one
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "KITCHEN_"))}
        self.env.update(HOME=str(self.home), KITCHEN_STATE=str(self.home / "state"), KITCHEN_CONFIG=str(self.home / "config"),
                        KITCHEN_DENYLIST=str(self.home / "no-denylist.txt"))
        self.procs = []

    def tearDown(self):
        for name in ("a", "b", "c"):
            (self.work / f"{name}.release").touch()  # also ends a holder's command a killed `kitchen slot` left running
        for proc in self.procs:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=TIMEOUT)
        self.tmp.cleanup()

    def slots(self, text):
        (self.home / "config").mkdir(exist_ok=True)
        (self.home / "config" / "slots.toml").write_text(text)

    def popen(self, argv, cwd=None):
        proc = subprocess.Popen(argv, cwd=cwd or self.work, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.procs.append(proc)
        return proc

    def start(self, *command):
        return self.popen([sys.executable, str(KITCHEN), "slot", "--", *command])

    def run_slot(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(KITCHEN), "slot", *args], cwd=cwd or self.work, env=self.env,
                              capture_output=True, text=True, timeout=TIMEOUT)

    def holder(self, name):
        """A command that logs its start, waits for <name>.release, then logs its end."""
        script = (f"echo {name} start >> log; touch {name}.started; "
                  f"while [ ! -e {name}.release ]; do sleep 0.1; done; echo {name} done >> log")
        return self.start("sh", "-c", script)

    def wait_for(self, name):
        deadline = time.monotonic() + TIMEOUT
        while not (self.work / name).exists():
            self.assertLess(time.monotonic(), deadline, f"{name} never appeared")
            time.sleep(0.05)

    def stderr_line(self, proc):
        box = []
        reader = threading.Thread(target=lambda: box.append(proc.stderr.readline()), daemon=True)
        reader.start()
        reader.join(TIMEOUT)
        self.assertTrue(box and box[0], "no line on stderr")
        return box[0]

    def finish(self, proc):
        out, err = proc.communicate(timeout=TIMEOUT)
        return proc.returncode, out, err

    def records(self):
        path = self.home / "state" / "slots" / "runs.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def record(self, part):
        """The one record whose command contains `part`: runs end in any order."""
        (found,) = [r for r in self.records() if part in r["command"]]
        return found

    def log(self):
        return (self.work / "log").read_text().split("\n")[:-1]


class QueueTests(SlotFixture):
    def test_with_one_slot_the_second_command_starts_only_after_the_first_exits(self):
        self.slots("slots = 1\n")
        first = self.holder("a")
        self.wait_for("a.started")
        held_from = time.monotonic()
        second = self.start("sh", "-c", "echo b start >> log")

        waiting = self.stderr_line(second)
        waiting_from = time.monotonic()
        started_while_held = (self.work / "log").read_text()
        (self.work / "a.release").touch()
        released = time.monotonic()

        self.assertEqual([self.finish(p)[0] for p in (first, second)], [0, 0])
        self.assertIn("number 1 in the queue, for one of 1 slot; 0: held by pid", waiting)
        self.assertIn("a.release", waiting, "the waiting line names the holder's command")
        self.assertEqual(started_while_held, "a start\n")
        self.assertEqual(self.log(), ["a start", "a done", "b start"])
        later, holder = self.record("b start"), self.record("a.release")
        self.assertEqual((later["slot"], later["exit"]), (0, 0))
        self.assertGreaterEqual(later["waited_s"], released - waiting_from - ROUNDING, "waited at least while the test held the slot")
        self.assertGreaterEqual(holder["run_s"], released - held_from - ROUNDING, "ran at least until the test released it")

    def test_with_two_slots_two_commands_run_at_once_and_a_third_waits(self):
        self.slots("slots = 2\n")
        first, second = self.holder("a"), self.holder("b")
        self.wait_for("a.started")
        self.wait_for("b.started")  # both run together: neither is released yet
        third = self.start("sh", "-c", "echo c start >> log; echo $KITCHEN_SLOT")

        waiting = self.stderr_line(third)
        self.assertFalse("c start" in (self.work / "log").read_text())
        (self.work / "a.release").touch()
        code, out, _ = self.finish(third)
        (self.work / "b.release").touch()

        self.assertIn("for one of 2 slots", waiting)
        self.assertEqual(code, 0)
        self.assertEqual([self.finish(p)[0] for p in (first, second)], [0, 0])
        self.assertEqual(out.strip(), str(self.record("a.release")["slot"]), "the third took the slot the first one freed")

    def test_waiters_run_in_order_of_arrival(self):
        self.slots("slots = 1\n")
        holder = self.holder("a")
        self.wait_for("a.started")
        early = self.start("sh", "-c", "echo early >> log")
        early_line = self.stderr_line(early)
        late = self.start("sh", "-c", "echo late >> log")
        late_line = self.stderr_line(late)

        (self.work / "a.release").touch()

        self.assertEqual([self.finish(p)[0] for p in (holder, early, late)], [0, 0, 0])
        self.assertIn("number 1 in the queue", early_line)
        self.assertIn("number 2 in the queue", late_line)
        self.assertEqual(self.log(), ["a start", "a done", "early", "late"])

    def test_a_newcomer_does_not_take_a_free_slot_from_an_older_waiter(self):
        queue = self.home / "state" / "slots" / "queue"
        queue.mkdir(parents=True)
        older = os.open(queue / f"{1:020d}-1", os.O_RDWR | os.O_CREAT, 0o600)  # a live waiter: its flock is held
        fcntl.flock(older, fcntl.LOCK_EX)
        newcomer = self.start("touch", "ran")

        waiting = self.stderr_line(newcomer)
        ran_while_queued = (self.work / "ran").exists()
        os.close(older)  # the older waiter leaves, or dies: either way its lock is gone

        self.assertEqual(self.finish(newcomer)[0], 0)
        self.assertIn("number 2 in the queue", waiting)
        self.assertFalse(ran_while_queued, "the slot was free, but an older waiter was first")
        self.assertTrue((self.work / "ran").exists())
        self.assertEqual(list(queue.iterdir()), [], "the dead ticket was removed, and the newcomer took its own away")

    def test_a_waiter_killed_with_sigkill_does_not_block_the_ones_behind_it(self):
        self.slots("slots = 1\n")
        holder = self.holder("a")
        self.wait_for("a.started")
        doomed = self.start("sh", "-c", "echo doomed >> log")
        self.stderr_line(doomed)
        behind = self.start("sh", "-c", "echo behind >> log")
        self.assertIn("number 2 in the queue", self.stderr_line(behind))

        doomed.kill()
        doomed.wait(TIMEOUT)
        (self.work / "a.release").touch()

        self.assertEqual([self.finish(p)[0] for p in (holder, behind)], [0, 0])
        self.assertEqual(self.log(), ["a start", "a done", "behind"])

    def test_a_killed_holder_frees_its_slot(self):
        # the command keeps running after its `kitchen slot` dies: it must not have inherited the lock
        self.slots("slots = 1\n")
        holder = self.holder("a")
        self.wait_for("a.started")
        holder.kill()
        holder.wait(TIMEOUT)

        result = self.run_slot("--", "true")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("waiting", result.stderr)
        self.assertEqual(self.log(), ["a start"], "the orphaned command was still running while the slot was taken")

    def test_a_nested_call_with_one_slot_runs_at_once_and_records_once(self):
        self.slots("slots = 1\n")
        inner = "\"$0\" \"$1\" slot -- sh -c 'echo inner $KITCHEN_SLOT'"

        result = self.run_slot("--", "sh", "-c", inner, sys.executable, str(KITCHEN))

        self.assertEqual((result.returncode, result.stdout), (0, "inner 0\n"), result.stderr)
        self.assertNotIn("waiting", result.stderr)
        self.assertEqual([r["command"].startswith("sh -c") for r in self.records()], [True], "one run, the outer one")

    def test_waiting_and_state_show_the_holders(self):
        idle = self.run_slot()
        self.slots("slots = 1\n")
        holder = self.holder("a")
        self.wait_for("a.started")

        busy = self.run_slot()

        self.assertEqual(idle.returncode, 0, idle.stderr)
        default = max(1, os.cpu_count() // 4)
        self.assertIn(f"kitchen slot: {default} slot", idle.stdout)
        self.assertEqual(idle.stdout.count(": free"), default)
        self.assertIn(f"  0: held by pid {holder.pid} for ", busy.stdout)
        self.assertIn("a.release", busy.stdout)

    def test_a_long_command_still_names_its_holder(self):
        self.slots("slots = 1\n")
        padding = "x" * 9000
        holder = self.start("sh", "-c", f": {padding}; touch a.started; while [ ! -e a.release ]; do sleep 0.1; done")
        self.wait_for("a.started")

        busy = self.run_slot()

        self.assertIn(f"  0: held by pid {holder.pid} for ", busy.stdout)
        self.assertIn(padding, busy.stdout)


class CommandTests(SlotFixture):
    def test_the_exit_code_passes_through(self):
        codes = [self.run_slot("--", "sh", "-c", script).returncode for script in ("exit 0", "exit 7", "kill -TERM $$")]

        self.assertEqual(codes, [0, 7, 128 + signal.SIGTERM])
        self.assertEqual([r["exit"] for r in self.records()], [0, 7, 128 + signal.SIGTERM])

    def test_the_command_sees_its_slot_and_cpus(self):
        self.slots("slots = 1\n")

        result = self.run_slot("--", "sh", "-c", "echo $KITCHEN_SLOT $KITCHEN_SLOT_CPUS")

        self.assertEqual(result.stdout, f"0 {os.cpu_count()}\n", result.stderr)

    def test_a_run_is_recorded_with_its_wait_and_run_time(self):
        began = time.monotonic()
        result = self.run_slot("--", "sh", "-c", "exit 3")
        took = time.monotonic() - began

        self.assertEqual(result.returncode, 3)
        (record,) = self.records()
        self.assertEqual({k: record[k] for k in ("command", "project", "slot", "exit")},
                         {"command": "sh -c 'exit 3'", "project": "work", "slot": 0, "exit": 3})
        for key in ("waited_s", "run_s"):
            self.assertGreaterEqual(record[key], 0)
            self.assertLessEqual(record[key], took + ROUNDING, f"{key} cannot exceed the whole call the test timed")
        self.assertTrue(datetime.datetime.fromisoformat(record["ts"]))

    def test_sigterm_reaches_the_command(self):
        script = 'trap "echo TERM > got; exit 0" TERM; touch a.started; while :; do sleep 0.1; done'
        proc = self.start("sh", "-c", script)
        self.wait_for("a.started")

        proc.send_signal(signal.SIGTERM)

        self.assertEqual(self.finish(proc)[0], 0, "kitchen slot exits with the command's own code")
        self.assertEqual((self.work / "got").read_text(), "TERM\n")

    def test_a_signal_while_the_command_is_spawned_reaches_it(self):
        # the seam: the signal lands after the spawn returned and before run_command knows the child's pid
        slot = slot_module()
        real_spawn, spawned = slot.spawn, []

        def spawn_then_signal(command, env, sigmask):
            pid = real_spawn(command, env, sigmask)
            spawned.append(pid)
            os.kill(os.getpid(), signal.SIGTERM)
            return pid

        previous = {sig: signal.getsignal(sig) for sig in slot.FORWARDED}
        self.addCleanup(lambda: [signal.signal(sig, handler) for sig, handler in previous.items()])
        self.addCleanup(self.reap, spawned)
        env = {k: v for k, v in self.env.items() if k.startswith("KITCHEN_")}
        with mock.patch.dict(os.environ, env), mock.patch.object(slot, "spawn", spawn_then_signal), open(os.devnull, "w") as quiet:
            os.environ.pop("KITCHEN_SLOT", None)
            code = slot.run_command(["sleep", "300"], out=quiet)

        (record,) = self.records()
        self.assertEqual((code, record["exit"]), (128 + signal.SIGTERM, 128 + signal.SIGTERM), record)
        self.assertNotIn("interrupted", record, "the command ran and the signal killed it; nothing was orphaned")
        self.assertEqual(signal.pthread_sigmask(signal.SIG_BLOCK, []), set(), "the mask is back")

    @staticmethod
    def reap(pids):
        """A child an orphaning bug left running in this process: kill and reap it."""
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            except (ProcessLookupError, ChildProcessError):
                pass

    def test_the_command_does_not_inherit_the_blocked_signals(self):
        result = self.run_slot("--", "sh", "-c", "grep '^SigBlk' /proc/self/status")

        if not Path("/proc/self/status").exists():
            self.skipTest("no /proc on this system")
        self.assertEqual(result.stdout.split(), ["SigBlk:", "0000000000000000"], result.stderr)

    def test_a_wait_cut_short_is_recorded_as_never_run(self):
        self.slots("slots = 1\n")
        self.holder("a")
        self.wait_for("a.started")
        began = time.monotonic()
        waiter = self.start("sh", "-c", "echo b start >> log")
        self.stderr_line(waiter)

        waiter.send_signal(signal.SIGTERM)

        self.assertEqual(self.finish(waiter)[0], 128 + signal.SIGTERM)
        took = time.monotonic() - began
        self.assertEqual(self.log(), ["a start"])
        (record,) = self.records()
        self.assertEqual((record["slot"], record["run_s"], record["exit"], record["interrupted"]), (None, None, None, "SIGTERM"))
        self.assertLessEqual(record["waited_s"], took + ROUNDING)
        self.assertEqual(list((self.home / "state" / "slots" / "queue").iterdir()), [], "its ticket left with it")

    def test_a_slots_file_that_does_not_say_a_positive_integer_fails_and_runs_nothing(self):
        for text in ("slots =\n", "slots = 0\n", 'slots = "2"\n', "other = 1\n"):
            with self.subTest(text=text):
                self.slots(text)
                result = self.run_slot("--", "touch", "ran")
                self.assertEqual(result.returncode, 125, result.stderr)
                self.assertIn(str(self.home / "config" / "slots.toml"), result.stderr)
                self.assertFalse((self.work / "ran").exists())
        self.assertEqual(self.records(), [])


class ReportTests(SlotFixture):
    def test_report_sums_waits_and_runs_per_project_inside_the_window(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        ts = now.isoformat(timespec="seconds")
        old = (now - datetime.timedelta(days=10)).isoformat(timespec="seconds")
        lines = [{"ts": ts, "project": "shop", "waited_s": w, "run_s": r, "exit": 0}
                 for w, r in ((0, 10), (0, 20), (0, 30), (10, 40), (100, 50))]
        lines.append({"ts": ts, "project": "blog", "waited_s": 5, "run_s": None, "exit": None, "interrupted": "SIGINT"})
        lines.append({"ts": old, "project": "shop", "waited_s": 999, "run_s": 999, "exit": 0})
        folder = self.home / "state" / "slots"
        folder.mkdir(parents=True)
        (folder / "runs.jsonl").write_text("".join(json.dumps(line) + "\n" for line in lines) + "not json\n")

        result = self.run_slot("--report", "--since", "7d")

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {line.split()[0]: line for line in result.stdout.splitlines()[1:]}
        self.assertTrue(result.stdout.startswith("kitchen slot: 6 runs in the last 7d"), result.stdout)
        self.assertIn("5 runs · waited p50 0 s, p95 100 s, 1 over 60 s · ran p50 30 s, p95 50 s", rows["shop"])
        self.assertIn("1 run · waited p50 5 s, p95 5 s, 0 over 60 s · 1 never ran", rows["blog"])
        self.assertIn("6 runs · waited p50 0 s, p95 100 s, 1 over 60 s · ran p50 30 s, p95 50 s · 1 never ran", rows["all"])
        self.assertIn("1 unreadable line", result.stdout)


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


class IntegrateTests(SlotFixture):
    def setUp(self):
        super().setUp()
        self.source = self.work / "shop"
        self.source.mkdir()
        git(self.source, "init", "-q", "-b", "main")
        (self.source / "README.md").write_text("x\n")
        git(self.source, "add", ".")
        git(self.source, "commit", "-q", "-m", "init")
        git(self.source, "checkout", "-q", "-b", "feature-a")
        git(self.source, "commit", "-q", "--allow-empty", "-m", "a")
        self.marker = self.work / "checked"
        (self.home / "config").mkdir()
        (self.home / "config" / "integrate.toml").write_text(
            f'[projects.shop]\nbase = "main"\nchecks = [{json.dumps(f"echo $KITCHEN_SLOT >> {self.marker}")}]\n')
        self.slots("slots = 1\n")

    def integrate(self):
        return self.popen([sys.executable, str(KITCHEN), "integrate", "--repo", str(self.source), "feature-a"])

    def integrate_record(self):
        lines = (self.home / "state" / "integrate" / "shop.jsonl").read_text().splitlines()
        return json.loads(lines[-1])

    def test_integrate_waits_for_the_only_slot_and_its_checks_run_inside_it(self):
        holder = self.holder("a")
        self.wait_for("a.started")
        integ = self.integrate()

        waiting = self.stderr_line(integ)
        checked_while_held = self.marker.exists()
        (self.work / "a.release").touch()
        code, out, err = self.finish(integ)

        self.assertIn("number 1 in the queue, for one of 1 slot", waiting)
        self.assertFalse(checked_while_held)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.marker.read_text(), "0\n", "the check runs with KITCHEN_SLOT set")
        self.assertEqual(self.finish(holder)[0], 0)
        last = self.record("kitchen integrate")
        self.assertEqual((last["command"], last["project"], last["exit"]), ("kitchen integrate feature-a", "shop", 0))

    def test_a_merge_hook_that_takes_a_slot_does_not_deadlock_integrate(self):
        hooks = self.work / "hooks"
        hooks.mkdir()
        hook = hooks / "pre-merge-commit"
        hook.write_text(f'#!/bin/sh\n"{sys.executable}" "{KITCHEN}" slot -- sh -c \'echo "hook $KITCHEN_SLOT" >> {self.marker}\'\n')
        hook.chmod(0o755)
        (self.home / ".gitconfig").write_text(f"[core]\n\thooksPath = {hooks}\n")

        code, out, err = self.finish(self.integrate())

        self.assertEqual(code, 0, out + err)
        self.assertEqual(self.marker.read_text(), "hook 0\n0\n", "the hook ran inside integrate's slot, then the check")

    def test_a_bad_slots_file_fails_integrate_with_the_reason(self):
        self.slots("slots = 0\n")

        code, out, err = self.finish(self.integrate())

        self.assertEqual(code, 1, out + err)
        self.assertNotIn("Traceback", err)
        self.assertIn("slots.toml: `slots` must be a positive integer", out)
        self.assertIn("slots.toml", self.integrate_record()["reason"])
        self.assertFalse(self.marker.exists())

    def test_sigterm_while_integrate_waits_is_recorded_by_both(self):
        self.holder("a")
        self.wait_for("a.started")
        integ = self.integrate()
        self.stderr_line(integ)

        integ.send_signal(signal.SIGTERM)
        code, out, err = self.finish(integ)

        self.assertEqual(code, 1, out + err)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.integrate_record()["reason"], "interrupted by SIGTERM")
        waited = self.record("kitchen integrate")
        self.assertEqual((waited["interrupted"], waited["run_s"], waited["slot"]), ("SIGTERM", None, None))
        self.assertFalse(self.marker.exists())


class CheckCostTests(SlotFixture):
    def test_check_cost_waits_for_the_only_slot_and_its_runs_hold_it(self):
        source = self.work / "shop"
        source.mkdir()
        git(source, "init", "-q", "-b", "main")
        git(source, "commit", "-q", "--allow-empty", "-m", "init")
        marker = self.work / "checked"
        self.slots("slots = 1\n")
        holder = self.holder("a")
        self.wait_for("a.started")
        cost = self.popen([sys.executable, str(KITCHEN), "check-cost", str(source), "--command", f"echo $KITCHEN_SLOT >> {marker}"])

        waiting = self.stderr_line(cost)
        ran_while_held = marker.exists()
        (self.work / "a.release").touch()
        code, out, err = self.finish(cost)

        self.assertIn("number 1 in the queue, for one of 1 slot", waiting)
        self.assertFalse(ran_while_held)
        self.assertEqual(code, 0, out + err)
        self.assertEqual(marker.read_text(), "0\n0\n0\n", "warm, same and moved each ran inside the slot")
        self.assertEqual(self.finish(holder)[0], 0)
        record = self.record("kitchen check-cost")
        self.assertEqual((record["project"], record["slot"], record["exit"]), ("shop", 0, 0))


class TemplateTests(SlotFixture):
    def check_repo(self):
        repo = self.work / "repo"
        (repo / "bin").mkdir(parents=True)
        shutil.copy(ROOT / "skills" / "chef-mode" / "playbooks" / "init" / "check.sh", repo / "bin" / "check")
        (repo / "bin" / "check").chmod(0o755)
        git(repo, "init", "-q")
        return repo

    def check(self, repo, tier, path):
        return subprocess.run(["bin/check", tier], cwd=repo, env={**self.env, "PATH": path}, capture_output=True, text=True, timeout=TIMEOUT)

    def test_the_heavy_tiers_take_a_slot_where_the_kitchen_is_installed_and_commit_does_not(self):
        repo = self.check_repo()
        fakebin = self.work / "fakebin"
        fakebin.mkdir()
        (fakebin / "kitchen").write_text(f'#!/bin/sh\nexec "{sys.executable}" "{KITCHEN}" "$@"\n')
        (fakebin / "kitchen").chmod(0o755)
        path = f"{fakebin}{os.pathsep}{self.env['PATH']}"

        commit, heavy, listing = (self.check(repo, tier, path) for tier in ("commit", "integrate", "--list"))

        self.assertEqual((commit.returncode, heavy.returncode), (1, 1))
        self.assertIn("bin/check commit: no command yet", commit.stderr)
        self.assertIn("bin/check integrate: no command yet", heavy.stderr)
        self.assertEqual(listing.stdout.split(), ["commit", "integrate", "nightly", "verify-tree"])
        self.assertEqual([(r["command"], r["project"], r["exit"]) for r in self.records()], [("bin/check integrate", "repo", 1)])

    def test_without_the_kitchen_the_template_runs_directly(self):
        bare_path = "/usr/bin:/bin"
        if shutil.which("kitchen", path=bare_path):
            self.skipTest("a kitchen is installed in /usr/bin or /bin")
        repo = self.check_repo()

        tier = self.check(repo, "integrate", bare_path)

        self.assertEqual(tier.returncode, 1)
        self.assertIn("bin/check integrate: no command yet", tier.stderr)
        self.assertEqual(self.records(), [])


if __name__ == "__main__":
    unittest.main()
