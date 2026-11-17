"""Every detector against its attack corpus in tests/corpus/<detector>/cases.json.

A corpus holds positive, negative and bypass cases. A miss found in review becomes a bypass entry, so the guarantee
is tested as a property over known attacks, not as one fixed example. Detection and redaction read the same cases.
"""
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

from tests.test_kitchen import KITCHEN, KitchenFixture

CORPUS = Path(__file__).resolve().parent / "corpus"


def load(detector):
    return json.loads((CORPUS / detector / "cases.json").read_text(encoding="utf-8"))


def joined(value):
    return "".join(value) if isinstance(value, list) else value


def classifier():
    """kitchen.transcripts, imported from the lib of this checkout."""
    sys.path.insert(0, str(KITCHEN.parent.parent / "lib"))
    try:
        from kitchen import transcripts
    finally:
        sys.path.pop(0)
    return transcripts


def scrub(text):
    return classifier().scrub(text)


class PublicSafetyCorpus(KitchenFixture):
    """`kitchen check` must report exactly the expected labels for each case file, working tree or index."""

    def run_corpus(self, detector):
        corpus = load(detector)
        denylist = None
        if "denylist" in corpus:
            denylist = self.home / "denylist.txt"
            denylist.write_text("\n".join(corpus["denylist"]) + "\n")
        for case in corpus["cases"]:
            path = self.repo / "cases" / f"{case['id']}.txt"
            path.parent.mkdir(exist_ok=True)
            path.write_text(joined(case["text"]) + "\n")
            if case.get("where") == "staged-only":
                subprocess.run(["git", "-C", str(self.repo), "add", "--", str(path)], check=True)
                path.write_text("clean\n")
        output = self.kitchen("check", "--lint-only", denylist=denylist).stdout
        for case in corpus["cases"]:
            prefix = f"cases/{case['id']}.txt" + (" (staged)" if case.get("where") == "staged-only" else "") + ":"
            reported = {line.split(": ", 1)[1].removesuffix(" in a public repo")
                        for line in output.splitlines() if line.startswith(f"FAIL  {prefix}")}
            with self.subTest(case["id"], kind=case["kind"]):
                self.assertEqual(reported, set(case["check"]), output)

    def test_credentials_corpus_detection(self):
        self.run_corpus("credentials")

    def test_private_terms_corpus_detection(self):
        self.run_corpus("private-terms")


class RedactionCorpus(KitchenFixture):
    """kitchen.transcripts.scrub, in process, with the corpus denylist (or none) instead of the real one."""

    def run_corpus(self, detector):
        corpus = load(detector)
        denylist = self.home / "denylist.txt"
        if "denylist" in corpus:
            denylist.write_text("\n".join(corpus["denylist"]) + "\n")
        previous = os.environ.get("KITCHEN_DENYLIST")
        os.environ["KITCHEN_DENYLIST"] = str(denylist)
        try:
            for case in corpus["cases"]:
                if "redact" not in case:
                    continue
                text = joined(case["text"])
                with self.subTest(case["id"], kind=case["kind"]):
                    scrubbed = scrub(text)
                    if case["redact"] == "keeps":
                        self.assertEqual(scrubbed, text)
                    else:
                        for secret in case["secret"]:
                            self.assertNotIn(secret, scrubbed)
        finally:
            if previous is None:
                os.environ.pop("KITCHEN_DENYLIST")
            else:
                os.environ["KITCHEN_DENYLIST"] = previous

    def test_credentials_corpus_redaction(self):
        self.run_corpus("credentials")

    def test_private_terms_corpus_redaction(self):
        self.run_corpus("private-terms")


class ProvenanceCorpus(KitchenFixture):
    """`kitchen retro` classifies each corpus transcript exactly as expected."""

    def write_case(self, home, case):
        for relative, events in case["transcripts"].items():
            base = home / ".claude" / "projects" if case["tool"] == "claude" else home / ".codex" / "sessions" / "2099" / "01" / "01"
            self.write_events(base / relative, events)
        for relative, events in case.get("claude_transcripts", {}).items():  # Claude-side evidence of a Codex case
            self.write_events(home / ".claude" / "projects" / relative, events)
        if "t3_scheduled_tasks" in case:  # T3 Code's state database: the scheduled_tasks rows, each {prompt, created_at}
            state = home / ".t3" / "userdata"
            state.mkdir(parents=True)
            with closing(sqlite3.connect(state / "statev2.sqlite")) as db:
                db.execute("CREATE TABLE scheduled_tasks (task_id TEXT PRIMARY KEY, title TEXT, prompt TEXT, created_at TEXT)")
                db.executemany("INSERT INTO scheduled_tasks VALUES (?, ?, ?, ?)",
                               [(f"task-{i}", "corpus", row["prompt"], row["created_at"]) for i, row in enumerate(case["t3_scheduled_tasks"])])
                db.commit()
        for automation in case.get("automations", []):
            folder = home / ".codex" / "automations" / automation["id"]
            folder.mkdir(parents=True)
            (folder / "automation.toml").write_text(
                f'version = 1\nid = "{automation["id"]}\"\nkind = "{automation["kind"]}"\nprompt = {json.dumps(automation["prompt"])}\n')

    def write_events(self, path, events):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e, separators=(",", ":")) + "\n" for e in events))

    def test_provenance_corpus(self):
        for case in load("provenance")["cases"]:
            home = self.home / "cases" / case["id"]
            home.mkdir(parents=True)
            self.write_case(home, case)
            env = {**os.environ, "HOME": str(home), "KITCHEN_REPO": str(self.repo), "KITCHEN_STATE": str(home / "state"),
                   "KITCHEN_CONFIG": str(home / "config"), "KITCHEN_DENYLIST": str(home / "none")}
            result = subprocess.run([sys.executable, str(KITCHEN), "retro", "--since", "1d", "--json", "--include-automated", "--tool", case["tool"]],
                                    env=env, capture_output=True, text=True, cwd=home)
            with self.subTest(case["id"], kind=case["kind"]):
                self.assertEqual(result.returncode, 0, result.stderr)
                got = [[p["text"], p["excluded"]] for p in json.loads(result.stdout)]
                self.assertEqual(got, case["expect"])

    def run_retro(self, home, *args):
        env = {**os.environ, "HOME": str(home), "KITCHEN_REPO": str(self.repo), "KITCHEN_STATE": str(home / "state"),
               "KITCHEN_CONFIG": str(home / "config"), "KITCHEN_DENYLIST": str(home / "none")}
        return subprocess.run([sys.executable, str(KITCHEN), "retro", "--since", "1d", *args], env=env, capture_output=True, text=True, cwd=home)

    def test_t3_database_without_scheduled_tasks_fails_loudly(self):
        home = self.home / "cases" / "t3-database-without-table"
        state = home / ".t3" / "userdata" / "statev2.sqlite"
        state.parent.mkdir(parents=True)
        with closing(sqlite3.connect(state)) as db:
            db.execute("CREATE TABLE other_table (id TEXT)")
            db.commit()
        result = self.run_retro(home, "--json", "--tool", "codex")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.strip(),
                         f"retro: cannot read T3's scheduled tasks from {state}: no such table: scheduled_tasks; "
                         "without them scheduled prompts cannot be told from yours")

    def test_reviewer_brief_template_is_recognized(self):
        """The reviewer brief of skills/second-opinion starts with its template's first line. Filled for each kind of
        review, that line must still match the classifier: if the wording drifts, this fails."""
        template = (KITCHEN.parent.parent / "skills" / "second-opinion" / "reviewer-prompt.md").read_text(encoding="utf-8")
        first = next(line for line in template.splitlines() if line.startswith("You are the <LENS> reviewer"))
        for kind in ("code change", "design review", "design+diff review"):
            filled = first.replace("<LENS>", "Skeptic").replace("<code change | design review | design+diff review>", kind)
            with self.subTest(kind=kind):
                self.assertNotIn("<", filled)
                self.assertIsNotNone(classifier().REVIEW_BRIEF.match(filled), filled)
