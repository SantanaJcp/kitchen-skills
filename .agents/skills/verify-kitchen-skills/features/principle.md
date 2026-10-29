# principle

- **Reached by:** `kitchen principle [<id|name>]`; chef-mode's `## Principles` index tells agents to run it, or read the linked file, before they apply a principle
- **Observable result:** an id (`prove`) prints its section of PRINCIPLES.md; a leaf name (`explain-the-number`) prints `principles/<name>.md`; no name prints chef-mode's index; an unknown name exits 1 and lists every valid id and leaf
- **Proved by:** tests/test_principles.py (PrincipleCommand)
