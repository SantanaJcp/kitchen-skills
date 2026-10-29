"""`kitchen principle [<id|name>]`: print one principle at the moment an agent applies it.

A principle id prints its section of PRINCIPLES.md, a leaf name prints principles/<name>.md, and no argument prints
the index in chef-mode's SKILL.md. An unknown name fails and lists the valid ones; nothing stands in for it.
"""
from __future__ import annotations

import re
from pathlib import Path

HEADING = re.compile(r"^## \d+\. .+? \(`([a-z-]+)`\)\s*$", re.MULTILINE)
INDEX_HEADING = "## Principles\n"


class PrincipleError(Exception):
    pass


def read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise PrincipleError(f"cannot read {path}: {error.strerror}") from error


def sections(repo: Path) -> dict[str, str]:
    """{id: its section of PRINCIPLES.md, from its heading up to the next one}."""
    text = read(repo / "PRINCIPLES.md")
    starts = list(HEADING.finditer(text))
    if not starts:
        raise PrincipleError(f"{repo / 'PRINCIPLES.md'} has no `## N. Title (`id`)` heading")
    ends = [m.start() for m in starts[1:]] + [len(text)]
    return {m.group(1): text[m.start():end].rstrip() + "\n" for m, end in zip(starts, ends)}


def leaves(repo: Path) -> dict[str, Path]:
    folder = repo / "principles"
    return {p.stem: p for p in sorted(folder.glob("*.md"))} if folder.is_dir() else {}


def index(repo: Path) -> str:
    text = read(repo / "skills" / "chef-mode" / "SKILL.md")
    start = text.find(INDEX_HEADING)
    if start == -1:
        raise PrincipleError("chef-mode's SKILL.md has no `## Principles` section")
    end = text.find("\n## ", start + len(INDEX_HEADING))
    return text[start:end if end != -1 else len(text)].rstrip() + "\n"


def show(repo: Path, name: str | None) -> str:
    if name is None:
        return index(repo)
    found = sections(repo)
    if name in found:
        return found[name]
    leaf = leaves(repo)
    if name in leaf:
        return read(leaf[name])
    raise PrincipleError(f"no principle {name!r}. ids: {', '.join(found)}. leaves: {', '.join(leaf) or 'none'}")
