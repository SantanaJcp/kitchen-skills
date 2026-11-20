# check

- **Reached by:** `bin/kitchen check [--lint-only]`
- **Observable result:** skill lint, private-data and credential scan (working tree and staged blobs), and the full unittest suite
- **Proved by:** tests/test_kitchen.py (LintTests, PublicSafetyTests, CheckRunsTestsHermeticallyTests), tests/test_corpus.py
