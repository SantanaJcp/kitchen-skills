"""Every control names the principle it enforces, and every name is a principle in PRINCIPLES.md."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NOT_CONTROLS = {
    "automation/bin/install-schedule": "writes launchd and systemd units; nightly-guard is the control",
    "lib/kitchen/__init__.py": "package marker",
    "lib/kitchen/common.py": "paths and git helpers",
    "lib/kitchen/environment.py": "tool and schedule probes that doctor prints",
}
CANDIDATES = [
    REPO / "bin" / "kitchen",
    *REPO.glob(".github/workflows/*.yml"),
    *REPO.glob("hooks/*"),
    *REPO.glob("skills/*/SKILL.md"),
    *REPO.glob("automation/bin/*"),
    *REPO.glob("lib/kitchen/*.py"),
]
CONTROLS = sorted(p for p in CANDIDATES if p.is_file() and p.name != "README.md" and str(p.relative_to(REPO)) not in NOT_CONTROLS)
HEADING = re.compile(r"^## \d+\. .+? \(`([a-z-]+)`\)\s*$", re.MULTILINE)
DECLARATION = re.compile(r"Principles(?: \([^)]*\))?:\s*(.+)")
NAME = re.compile(r"`([a-z][a-z-]*)`")

CHEF_MODE = REPO / "skills" / "chef-mode" / "SKILL.md"
OWNER_TYPED = {
    "wait-what": "the owner types it when a reply did not land; no request routes to it",
    "sharpen": "the owner types it on a prompt of their own; no request routes to it",
}
LEAVES = REPO / "principles"
LEAF_LINK = re.compile(r"\]\(principles/([a-z0-9-]+)\.md\)")
PARENT = re.compile(r"^- \*\*Parent:\*\* `([a-z-]+)`", re.MULTILINE)
WHEN = re.compile(r"^- \*\*When:\*\* \S", re.MULTILINE)
RELATIVE_LINK = re.compile(r"\]\((?!https?:)([^)#\s]+)")
CHEF_MODE_MAX_LINES, CHEF_MODE_MAX_BYTES = 90, 12_000


def declared(path: Path) -> list[str]:
    match = DECLARATION.search(path.read_text(encoding="utf-8"))
    return NAME.findall(match.group(1)) if match else []


def enforced_by_a_control(controls) -> set[str]:
    """chef-mode names every principle to switch them on; it enforces none, so it cannot vouch for one."""
    return {n for path in controls if path != CHEF_MODE for n in declared(path)}


def principle_sections() -> dict[str, str]:
    text = (REPO / "PRINCIPLES.md").read_text(encoding="utf-8")
    return dict(zip(HEADING.findall(text), re.split(r"^## \d+\. ", text, flags=re.MULTILINE)[1:]))


def chef_mode_principles_section() -> str:
    text = CHEF_MODE.read_text(encoding="utf-8")
    match = re.search(r"^## Principles\n(.*?)(?=^## )", text, re.MULTILINE | re.DOTALL)
    return match.group(1) if match else ""


class Principles(unittest.TestCase):
    def setUp(self):
        self.ids = HEADING.findall((REPO / "PRINCIPLES.md").read_text(encoding="utf-8"))

    def test_principles_parse_with_unique_ids(self):
        self.assertEqual(len(self.ids), 13, "PRINCIPLES.md headings must read `## N. Title (`id`)`")
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_every_control_names_known_principles(self):
        for path in CONTROLS:
            with self.subTest(control=str(path.relative_to(REPO))):
                names = declared(path)
                self.assertTrue(names, "no `Principles:` line naming at least one principle")
                self.assertEqual([n for n in names if n not in self.ids], [], "names a principle PRINCIPLES.md does not define")

    def test_every_excused_helper_still_exists(self):
        self.assertEqual([p for p in NOT_CONTROLS if not (REPO / p).is_file()], [], "drop a gone helper from NOT_CONTROLS")

    def test_reviewer_effort_lives_only_in_models_toml(self):
        level = re.compile(r"\b(low|medium|high|xhigh|max)\s+(reasoning\s+)?effort\b", re.IGNORECASE)
        for rel in ("PRINCIPLES.md", "skills/chef-mode/SKILL.md", "skills/second-opinion/SKILL.md"):
            with self.subTest(file=rel):
                text = (REPO / rel).read_text(encoding="utf-8")
                self.assertEqual([m.group(0) for m in level.finditer(text)], [], "set effort in models.toml instead")

    def test_second_opinion_routes_three_vendors_and_checks_model_vendor(self):
        text = (REPO / "skills" / "second-opinion" / "SKILL.md").read_text(encoding="utf-8")
        for token in ("`claude`", "`codex`", "`gemini`", "`claudeAgent`", "`antigravity`", "`reasoningEffort`", "`thought_level`"):
            with self.subTest(token=token):
                self.assertIn(token, text, "second-opinion must document all three vendors, T3 providers, and effort options")
        self.assertRegex(text, r"model's vendor.*not the provider", "self-review check must compare model vendor, not provider alone")
        for pair in ("`effort` for `claudeAgent`", "`reasoningEffort` for `codex`", "`thought_level` for `antigravity`"):
            with self.subTest(pair=pair):
                self.assertIn(pair, text, "each T3 provider maps effort to its own option")
        self.assertIn("`target.model`", text, "T3 reviewers must pin the configured model, not leave the choice to T3")

    def test_chef_mode_names_and_lists_every_principle_and_leaf(self):
        self.assertEqual(sorted(declared(CHEF_MODE)), sorted(self.ids))
        self.assertEqual(enforced_by_a_control([CHEF_MODE]), set())
        section = chef_mode_principles_section()
        self.assertTrue(section, "chef-mode has no `## Principles` section")
        self.assertEqual(re.findall(r"^\d+\.\s+\[`([a-z-]+)`\]\(PRINCIPLES\.md#[^)]+\)", section, re.MULTILINE), self.ids)
        parent_of = {}
        for line in section.splitlines():
            m = re.match(r"^\d+\.\s+\[`([a-z-]+)`\]", line)
            if m:
                parent_of.update({name: m.group(1) for name in LEAF_LINK.findall(line)})
        sections = principle_sections()
        expected = {name: pid for pid in self.ids for name in LEAF_LINK.findall(sections[pid])}
        self.assertEqual(parent_of, expected, "chef-mode leaves must match PRINCIPLES.md")

    def test_chef_mode_routes_every_skill_and_stays_short(self):
        text = CHEF_MODE.read_text(encoding="utf-8")
        self.assertLessEqual(text.count("\n"), CHEF_MODE_MAX_LINES)
        self.assertLessEqual(len(text.encode("utf-8")), CHEF_MODE_MAX_BYTES)
        section = text.split("## Playbooks", 1)[1].split("\n## ", 1)[0]
        routed = {name for row in section.splitlines() if row.startswith("|")
                  for name in NAME.findall(row.rstrip(" |").rsplit("|", 1)[-1])}
        skills = {p.parent.name for p in REPO.glob("skills/*/SKILL.md")} - {"chef-mode"}
        self.assertEqual(len(skills), 13, "all 14 skills stay")
        self.assertEqual(sorted(skills - routed - set(OWNER_TYPED)), [], "add a row to chef-mode's playbook table")
        self.assertEqual(sorted(set(OWNER_TYPED) - skills), [], "drop a gone skill from OWNER_TYPED")

    def test_chef_mode_symlinks_principles_md_and_leaves(self):
        self.assertTrue((REPO / "skills" / "chef-mode" / "PRINCIPLES.md").is_symlink())
        self.assertEqual((REPO / "skills" / "chef-mode" / "PRINCIPLES.md").resolve(), (REPO / "PRINCIPLES.md").resolve())
        self.assertTrue((REPO / "skills" / "chef-mode" / "principles").is_symlink())
        self.assertEqual((REPO / "skills" / "chef-mode" / "principles").resolve(), LEAVES.resolve())

    def test_all_24_leaves_are_linked_and_well_formed(self):
        files = sorted(LEAVES.glob("*.md"))
        self.assertEqual(len(files), 24, "each of pstack's 24 principles gets a leaf in principles/")
        principles_md = set(LEAF_LINK.findall((REPO / "PRINCIPLES.md").read_text(encoding="utf-8")))
        self.assertEqual(principles_md, {p.stem for p in files})
        linked_from = {name: pid for pid, sec in principle_sections().items() for name in LEAF_LINK.findall(sec)}
        for path in files:
            with self.subTest(leaf=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("# "))
                self.assertEqual(PARENT.findall(text), [linked_from.get(path.stem)])
                self.assertRegex(text, WHEN)
                self.assertLessEqual(text.count("\n"), 25)
                self.assertEqual([t for t in RELATIVE_LINK.findall(text) if not (path.parent / t).exists()], [])

    def test_every_principle_is_enforced_or_says_rule_only(self):
        sections = principle_sections()
        cited = enforced_by_a_control(CONTROLS)
        for pid in self.ids:
            with self.subTest(principle=pid):
                enforced = re.search(r"^- \*\*Enforced by:\*\* (.+)$", sections[pid], re.MULTILINE)
                self.assertTrue(enforced, "no `Enforced by:` line")
                self.assertTrue(pid in cited or enforced.group(1).startswith("rule only"))


if __name__ == "__main__":
    unittest.main()
