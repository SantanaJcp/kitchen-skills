"""The verify-kitchen-skills feature map covers every `kitchen` command, with no orphaned files."""
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FEATURES = REPO / ".agents" / "skills" / "verify-kitchen-skills" / "features"
COMMAND = re.compile(r'\bsub\.add_parser\("([a-z-]+)"')


class FeatureMap(unittest.TestCase):
    def test_every_command_has_a_feature_file_listed_in_the_readme(self):
        commands = COMMAND.findall((REPO / "bin" / "kitchen").read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(commands), 6, "the command pattern stopped matching bin/kitchen")
        readme = (FEATURES / "README.md").read_text(encoding="utf-8")
        for name in commands:
            with self.subTest(command=name):
                self.assertTrue((FEATURES / f"{name}.md").is_file(), f"add features/{name}.md")
                self.assertIn(f"({name}.md)", readme, f"list {name} in features/README.md")
        existing = {p.stem for p in FEATURES.glob("*.md") if p.name != "README.md"}
        self.assertEqual(existing, set(commands), "remove orphaned feature files")


if __name__ == "__main__":
    unittest.main()
