"""Every control names the principle it enforces, and every name is a principle in PRINCIPLES.md.

A control is a guard, a scheduled job, a CLI module or a skill. They are found by
globbing, so a new one is held to this suite the moment it lands; a helper that enforces nothing is excused by name
in NOT_CONTROLS, with the reason.
"""
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOT_CONTROLS = {
    "hooks/shellparse.py": "the parser the guards share",
    "automation/bin/install-schedule": "writes launchd and systemd units; the jobs it schedules are the controls",
    "lib/kitchen/__init__.py": "package marker",
    "lib/kitchen/checkplan.py": "picks which test modules a commit runs",
    "lib/kitchen/common.py": "paths and git helpers",
    "lib/kitchen/environment.py": "tool and schedule probes that doctor prints",
    "lib/kitchen/inventory.py": "lists what the agents can see",
    "lib/kitchen/notify.py": "sends a local notification for the controls that decide to",
    "lib/kitchen/principles.py": "prints a principle for an agent to read; it decides nothing",
}
CANDIDATES = [REPO / "bin" / "kitchen", *REPO.glob(".github/workflows/*.yml"), *REPO.glob("hooks/*"), *REPO.glob("skills/*/SKILL.md"), *REPO.glob("automation/bin/*"), *REPO.glob("lib/kitchen/*.py")]
CONTROLS = sorted(p for p in CANDIDATES if p.is_file() and p.name != "README.md" and str(p.relative_to(REPO)) not in NOT_CONTROLS)
HEADING = re.compile(r"^## \d+\. .+? \(`([a-z-]+)`\)\s*$", re.MULTILINE)
DECLARATION = re.compile(r"Principles(?: \([^)]*\))?:\s*(.+)")
NAME = re.compile(r"`([a-z][a-z-]*)`")


CHEF_MODE = REPO / "skills" / "chef-mode" / "SKILL.md"
OWNER_TYPED = {
    "wait-what": "the owner types it when a reply did not land; no request routes to it",
}
LEAVES = REPO / "principles"
LEAF_LINK = re.compile(r"\]\(principles/([a-z0-9-]+)\.md\)")
PARENT = re.compile(r"^- \*\*Parent:\*\* `([a-z-]+)`", re.MULTILINE)
WHEN = re.compile(r"^- \*\*When:\*\* \S", re.MULTILINE)
RELATIVE_LINK = re.compile(r"\]\((?!https?:)([^)#\s]+)")
# Claude Code's docs say it re-attaches an invoked skill's first 5,000 tokens after compaction (not observed with a headless
# /compact, 2026-10-29); the index stays inside that budget either way
CHEF_MODE_MAX_LINES, CHEF_MODE_MAX_BYTES = 140, 18_000


def declared(path: Path) -> list[str]:
    match = DECLARATION.search(path.read_text(encoding="utf-8"))
    return NAME.findall(match.group(1)) if match else []


def enforced_by_a_control(controls) -> set[str]:
    """chef-mode names every principle to switch them on; it enforces none, so it cannot vouch for one."""
    return {n for path in controls if path != CHEF_MODE for n in declared(path)}


def principle_sections() -> dict[str, str]:
    text = (REPO / "PRINCIPLES.md").read_text(encoding="utf-8")
    return dict(zip(HEADING.findall(text), re.split(r"^## \d+\. ", text, flags=re.MULTILINE)[1:]))


def chef_mode_index() -> str:
    """chef-mode's `## Principles` section, or "" when it has none."""
    text = CHEF_MODE.read_text(encoding="utf-8")
    match = re.search(r"^## Principles\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


class Principles(unittest.TestCase):
    def setUp(self):
        self.ids = HEADING.findall((REPO / "PRINCIPLES.md").read_text(encoding="utf-8"))

    def test_principles_parse_with_unique_ids(self):
        self.assertGreaterEqual(len(self.ids), 13, "PRINCIPLES.md headings must read `## N. Title (`id`)`")
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_every_control_names_known_principles(self):
        for path in CONTROLS:
            with self.subTest(control=str(path.relative_to(REPO))):
                self.assertTrue(path.is_file(), "a listed control is missing")
                names = declared(path)
                self.assertTrue(names, "no `Principles:` line naming at least one principle")
                self.assertEqual([n for n in names if n not in self.ids], [], "names a principle PRINCIPLES.md does not define")

    def test_every_excused_helper_still_exists(self):
        self.assertEqual([p for p in NOT_CONTROLS if not (REPO / p).is_file()], [], "drop a gone helper from NOT_CONTROLS")

    def test_the_reviewer_effort_lives_only_in_kitchen_models(self):
        # one source of truth: a level written here would contradict what each person sets with `kitchen models`
        level = re.compile(r"\b(low|medium|high|xhigh|max)\s+(reasoning\s+)?effort\b", re.IGNORECASE)
        for rel in ("PRINCIPLES.md", "skills/chef-mode/SKILL.md", "skills/second-opinion/SKILL.md", "skills/second-opinion/reviewer-prompt.md"):
            with self.subTest(file=rel):
                text = (REPO / rel).read_text(encoding="utf-8")
                self.assertEqual([m.group(0) for m in level.finditer(text)], [], "set it in `kitchen models` instead")

    def test_chef_mode_names_every_principle(self):
        # chef-mode is the one place the kitchen's rules switch on; a principle it leaves out never reaches an agent
        self.assertEqual(sorted(declared(CHEF_MODE)), sorted(self.ids))

    def test_chef_mode_alone_vouches_for_no_principle(self):
        # regression: while chef-mode counted as a control, the check below could never fail (audit, 2026-10-26)
        self.assertEqual(enforced_by_a_control([CHEF_MODE]), set())

    def test_chef_mode_routes_every_skill(self):
        # every kitchen skill is manual-only: one that no playbook row names is reached only if the owner remembers it
        section = CHEF_MODE.read_text(encoding="utf-8").split("## Playbooks", 1)[1].split("\n## ", 1)[0]
        routed = {name for row in section.splitlines() if row.startswith("|")
                  for name in NAME.findall(row.rstrip(" |").rsplit("|", 1)[-1])}
        skills = {p.parent.name for p in REPO.glob("skills/*/SKILL.md")} - {"chef-mode"}
        self.assertEqual(sorted(skills - routed - set(OWNER_TYPED)), [], "add a row to chef-mode's playbook table")
        self.assertEqual(sorted(set(OWNER_TYPED) - skills), [], "drop a gone skill from OWNER_TYPED")

    def test_chef_mode_reads_this_principles_file(self):
        link = REPO / "skills" / "chef-mode" / "PRINCIPLES.md"
        self.assertTrue(link.is_symlink(), "a copy would drift: link it to the repo's PRINCIPLES.md")
        self.assertEqual(link.resolve(), (REPO / "PRINCIPLES.md").resolve())

    def test_every_pstack_principle_has_a_leaf_linked_from_principles_md_and_chef_mode(self):
        files = {p.stem for p in LEAVES.glob("*.md")}
        self.assertEqual(len(files), 24, "each of pstack's 24 principles gets a leaf in principles/")
        principles_md = set(LEAF_LINK.findall((REPO / "PRINCIPLES.md").read_text(encoding="utf-8")))
        self.assertEqual(principles_md, files, "PRINCIPLES.md links a missing leaf, or leaves one out")
        self.assertEqual(set(LEAF_LINK.findall(chef_mode_index())), files, "chef-mode's index links a missing leaf, or leaves one out")

    def test_every_nested_principle_in_principles_md_is_a_link_to_its_leaf(self):
        for pid, section in principle_sections().items():
            nested = re.search(r"^- \*\*Includes \(pstack\):\*\*\n((?:  - .*\n)+)", section, re.MULTILINE)
            for line in nested.group(1).splitlines() if nested else []:
                with self.subTest(principle=pid, line=line[:40]):
                    self.assertRegex(line, r"^  - \[\*[^*]+\*\]\(principles/[a-z0-9-]+\.md\): \S", "one trigger line plus a link to the leaf")

    def test_each_leaf_names_its_parent_and_when_and_stays_short(self):
        files = sorted(LEAVES.glob("*.md"))
        self.assertTrue(files, "no leaves in principles/")
        linked_from = {name: pid for pid, section in principle_sections().items() for name in LEAF_LINK.findall(section)}
        for path in files:
            with self.subTest(leaf=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("# "), "starts with its title")
                self.assertEqual(PARENT.findall(text), [linked_from.get(path.stem)], "its Parent is the section of PRINCIPLES.md that links it")
                self.assertRegex(text, WHEN, "one `When:` line")
                self.assertLessEqual(text.count("\n"), 25, "a leaf is the rule in about 20 lines; detail belongs elsewhere")
                self.assertEqual([t for t in RELATIVE_LINK.findall(text) if not (path.parent / t).exists()], [], "broken link")

    def test_chef_mode_index_lists_every_principle_in_order_with_its_leaves_under_it(self):
        index = chef_mode_index()
        self.assertTrue(index, "chef-mode has no `## Principles` index")
        self.assertEqual(re.findall(r"^- `([a-z-]+)`", index, re.MULTILINE), self.ids)
        parent_of = {}
        for block in re.split(r"^(?=- `)", index, flags=re.MULTILINE)[1:]:
            pid = re.match(r"- `([a-z-]+)`", block).group(1)
            parent_of.update({name: pid for name in LEAF_LINK.findall(block)})
        sections = principle_sections()
        self.assertEqual(parent_of, {name: pid for pid in self.ids for name in LEAF_LINK.findall(sections[pid])},
                         "a leaf sits under a different parent in chef-mode than in PRINCIPLES.md")

    def test_chef_mode_fits_the_compaction_reattach_budget(self):
        text = CHEF_MODE.read_text(encoding="utf-8")
        self.assertLessEqual(text.count("\n"), CHEF_MODE_MAX_LINES)
        self.assertLessEqual(len(text.encode("utf-8")), CHEF_MODE_MAX_BYTES)

    def test_chef_mode_reaches_the_leaves(self):
        link = REPO / "skills" / "chef-mode" / "principles"
        self.assertTrue(link.is_symlink(), "a copy would drift: link it to the repo's principles/")
        self.assertEqual(link.resolve(), LEAVES.resolve())

    def test_every_principle_is_enforced_or_says_rule_only(self):
        text = (REPO / "PRINCIPLES.md").read_text(encoding="utf-8")
        sections = dict(zip(self.ids, re.split(r"^## \d+\. ", text, flags=re.MULTILINE)[1:]))
        cited = enforced_by_a_control(CONTROLS)
        for pid in self.ids:
            with self.subTest(principle=pid):
                enforced = re.search(r"^- \*\*Enforced by:\*\* (.+)$", sections[pid], re.MULTILINE)
                self.assertTrue(enforced, "no `Enforced by:` line")
                self.assertTrue(pid in cited or enforced.group(1).startswith("rule only"),
                                "no control names this principle, and its `Enforced by:` does not start with `rule only`")


class PrincipleCommand(unittest.TestCase):
    """`kitchen principle` reads the real repo's principles; HOME and the kitchen's state stay throwaway."""

    def kitchen(self, *args):
        with tempfile.TemporaryDirectory() as home:
            env = {**os.environ, "HOME": home, "KITCHEN_REPO": str(REPO), "KITCHEN_STATE": f"{home}/state",
                   "KITCHEN_CONFIG": f"{home}/config", "KITCHEN_DENYLIST": f"{home}/no-denylist.txt"}
            return subprocess.run([sys.executable, str(REPO / "bin" / "kitchen"), *args], env=env, capture_output=True, text=True)

    def test_an_id_prints_only_its_section_of_principles_md(self):
        result = self.kitchen("principle", "truthful-state")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("## 2. Unknown is never green (`truthful-state`)\n"), result.stdout[:80])
        self.assertIn("Never invent counts, freshness, progress or success.", result.stdout)
        self.assertIn("- **Enforced by:** `status` prints `unknown`", result.stdout)
        self.assertNotIn("(`prove`)", result.stdout)
        self.assertNotIn("(`no-fallbacks`)", result.stdout)

    def test_a_leaf_name_prints_its_file(self):
        result = self.kitchen("principle", "explain-the-number")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("# Explain the Number\n"), result.stdout[:80])
        self.assertIn("Write the decision down before you run.", result.stdout)
        self.assertEqual(result.stdout, (LEAVES / "explain-the-number.md").read_text(encoding="utf-8"))

    def test_no_name_prints_the_index_chef_mode_carries(self):
        result = self.kitchen("principle")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("## Principles\n"), result.stdout[:80])
        self.assertIn("[explain-the-number](principles/explain-the-number.md)", result.stdout)
        self.assertNotIn("## Playbooks", result.stdout)

    def test_an_unknown_name_fails_and_lists_the_valid_ones(self):
        result = self.kitchen("principle", "explain-the-numbers")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "", "nothing stands in for an unknown name")
        self.assertIn("'explain-the-numbers'", result.stderr)
        self.assertIn("truthful-state", result.stderr)
        self.assertIn("explain-the-number,", result.stderr)


if __name__ == "__main__":
    unittest.main()
