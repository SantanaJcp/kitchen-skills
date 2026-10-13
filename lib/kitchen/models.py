"""`kitchen models`: which models do each role, in each person's own setup (~/.config/kitchen/models.toml).

One role today, `reviewer`, the one `second-opinion` reads. It depends on who wrote the work, so it is set per author
family (`--author claude` means the work is Claude's and its reviewers run another family's model). A role is a list:
`set` replaces it with one model, `add` appends another, and second-opinion runs every lens on each, in parallel. A
new role is added when a workflow reads it, not before. Every entry is explicit: provider (the CLI, or T3's provider of
that name), model (`default` = that tool's own default), effort and service tier.

A role nobody set is an error with the command that sets it, never a default model; a reviewer running the author's
own family is refused, and so is a pi model whose family kitchen cannot tell. Whether the model exists is learned when
it is used: the tool's error is shown as it is. pi alone has no such error (it runs the nearest model it knows), so its
call is refused unless `pi --list-models` lists the id exactly.
Principles: `cross-review` (another model reviews), `no-fallbacks` (no stand-in model), `owner-attention` (set once).
"""
from __future__ import annotations

import json
import shlex
import subprocess
import tomllib
from pathlib import Path

from kitchen import common

AUTHORS = ("claude", "codex")  # who wrote the work
PROVIDERS = ("claude", "codex", "pi")  # what runs a role
ROLES = ("reviewer",)  # each set per author family
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TIERS = ("standard", "fast")
FIELDS = ("provider", "model", "effort", "tier")
# pi runs many vendors' models; the family is the prefix of its model id (`pi --list-models`)
PI_FAMILIES = {"anthropic": "claude", "claude-bridge": "claude", "openai": "codex", "openai-codex": "codex"}
PI_READ_ONLY = "read,grep,find,ls"


class ModelsError(Exception):
    pass


def path() -> Path:
    return common.config_dir() / "models.toml"


def load() -> dict:
    p = path()
    if not p.is_file():
        return {}
    try:
        return tomllib.loads(p.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ModelsError(f"cannot parse {p}: {error}; fix it or rerun `kitchen models set`") from error


def key(role: str, author: str | None) -> tuple[str, str]:
    if role not in ROLES:
        raise ModelsError(f"unknown role {role!r}: one of {', '.join(ROLES)}")
    if author not in AUTHORS:
        raise ModelsError(f"`{role}` depends on who wrote the work: pass --author {' or --author '.join(AUTHORS)}")
    return (role, author)


def set_command(role: str, author: str | None) -> str:
    who = f" --author {author}" if author else ""
    return f"kitchen models set {role}{who} --provider <{'|'.join(PROVIDERS)}> --model <id|default> --effort <{'|'.join(EFFORTS)}> --tier <{'|'.join(TIERS)}>"


def family(entry: dict) -> str:
    if entry["provider"] != "pi":
        return entry["provider"]
    prefix = entry["model"].split("/", 1)[0] if "/" in entry["model"] else None
    if prefix not in PI_FAMILIES:
        raise ModelsError(f"a pi model is <provider>/<id> with a provider kitchen knows the family of ({', '.join(PI_FAMILIES)}), "
                          f"so it can tell whose model reviews; not {entry['model']!r}")
    return PI_FAMILIES[prefix]


def validate(role: str, author: str | None, entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ModelsError(f"{role} for {author}: each entry is a table of {', '.join(FIELDS)}; run `{set_command(role, author)}`")
    missing = [f for f in FIELDS if not isinstance(entry.get(f), str) or not entry.get(f)]
    if missing:
        raise ModelsError(f"{role}{' for ' + author if author else ''}: missing {', '.join(missing)}; run `{set_command(role, author)}`")
    if entry["provider"] not in PROVIDERS:
        raise ModelsError(f"provider must be one of {', '.join(PROVIDERS)}, not {entry['provider']!r}")
    if entry["effort"] not in EFFORTS:
        raise ModelsError(f"effort must be one of {', '.join(EFFORTS)}, not {entry['effort']!r}")
    if entry["tier"] not in TIERS:
        raise ModelsError(f"tier must be one of {', '.join(TIERS)}, not {entry['tier']!r}")
    if entry["provider"] == "pi" and entry["tier"] == "fast":
        raise ModelsError("pi has no service tier, in its CLI or in T3; set --tier standard")
    if family(entry) == author:
        raise ModelsError(f"a {role} for {author}'s work must run another family's model: the author's model does not check its own work")
    return {f: entry[f] for f in FIELDS}


def raw(data: dict, role: str, author: str) -> list:
    if not isinstance(data.get(role, {}), dict):
        raise ModelsError(f"{path()}: {role} is not a table; fix it or rerun `{set_command(role, author)}`")
    node = data.get(role, {}).get(author)
    if node is None:
        return []
    if isinstance(node, dict):  # one table, as files were written before a role held several models
        return [node]
    if not isinstance(node, list):
        raise ModelsError(f"{path()}: {role}.{author} is neither a table nor a list of tables; fix it or rerun `{set_command(role, author)}`")
    return node


def current(role: str, author: str | None) -> list[dict]:
    return [validate(role, author, e) for e in raw(load(), *key(role, author))]


def get(role: str, author: str | None) -> list[dict]:
    entries = current(role, author)
    if not entries:
        raise ModelsError(f"{role}{' for ' + author + ' work' if author else ''} is not configured in {path()}: run `{set_command(role, author)}`")
    return entries


def put(role: str, author: str | None, entries: list[dict]) -> None:
    clean = [validate(role, author, e) for e in entries]
    pairs = [(e["provider"], e["model"]) for e in clean]
    if len(set(pairs)) < len(pairs):
        raise ModelsError(f"{role} for {author} work already runs that provider and model; `kitchen models remove` it first to change it")
    data = load()
    unknown = sorted(set(data) - set(ROLES)) + sorted(f"{r}.{a}" for r in ROLES if isinstance(data.get(r), dict) for a in set(data[r]) - set(AUTHORS))
    if unknown:  # rewriting the file would drop them silently
        raise ModelsError(f"{path()} has tables kitchen does not read ({', '.join(unknown)}); remove them by hand, then rerun")
    lists = {(r, a): [validate(r, a, e) for e in raw(data, r, a)] for r in ROLES for a in AUTHORS}
    lists[key(role, author)] = clean
    lines = ["# kitchen models: which models do each role. Written by `kitchen models set|add|remove`; each person sets their own.", ""]
    for (role_name, a), values in lists.items():
        for value in values:
            lines.append(f"[[{role_name}.{a}]]")
            lines += [f"{f} = {json.dumps(value[f])}" for f in FIELDS if f in value]
            lines.append("")
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


def remove(role: str, author: str | None, provider: str, model: str | None) -> None:
    entries = get(role, author)
    matches = [e for e in entries if e["provider"] == provider and model in (None, e["model"])]
    if not matches:
        raise ModelsError(f"no {role} for {author} work runs on {provider}{' ' + model if model else ''}")
    if len(matches) > 1:
        raise ModelsError(f"more than one {role} for {author} work runs on {provider}: pass --model")
    put(role, author, [e for e in entries if e is not matches[0]])


def pi_lists(model: str) -> None:
    """pi runs the nearest model it knows for an id it does not list, so a pi reviewer runs only on an id pi lists exactly."""
    try:
        listed = subprocess.run(["pi", "--list-models"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ModelsError(f"cannot list pi's models ({error}), so the pi reviewer cannot run") from error
    rows = {tuple(line.split()[:2]) for line in listed.stdout.splitlines()}
    if listed.returncode != 0 or tuple(model.split("/", 1)) not in rows:
        raise ModelsError(f"`pi --list-models` does not list {model}, and pi would run the nearest model it knows instead; "
                          f"set an id it lists exactly")


def command(entry: dict) -> list[str]:
    """The non-interactive CLI call for a role outside T3: `codex exec`, `claude -p` or `pi -p`. The prompt goes last."""
    if entry["provider"] == "codex":
        argv = ["codex", "exec", "--skip-git-repo-check", "-s", "read-only"]
        if entry["model"] != "default":
            argv += ["-m", entry["model"]]
        return argv + ["-c", f'model_reasoning_effort="{entry["effort"]}"',
                       "-c", f'service_tier="{"priority" if entry["tier"] == "fast" else "default"}"']
    if entry["provider"] == "pi":  # pi has no sandbox: read-only is its read tools alone, no shell and no MCP
        pi_lists(entry["model"])
        return ["pi", "-p", "--no-session", "--no-mcp", "--tools", PI_READ_ONLY, "--model", entry["model"], "--thinking", entry["effort"]]
    if entry["tier"] == "fast":
        raise ModelsError("the claude CLI has no flag for the fast tier; set --tier standard, or run the role where the tier can be set (T3's delegate_task)")
    argv = ["claude", "-p"]
    if entry["model"] != "default":
        argv += ["--model", entry["model"]]
    return argv + ["--effort", entry["effort"]]


def describe(entry: dict) -> str:
    return f"{entry['provider']} {entry['model']} · effort {entry['effort']} · tier {entry['tier']}"


def render() -> str:
    out = [f"kitchen models  ({path()})"]
    for role in ROLES:
        for author in AUTHORS:
            label = f"{role} for {author} work"
            try:
                for i, e in enumerate(get(role, author)):
                    out.append(f"  {label if i == 0 else '':<24} {describe(e)}")
            except ModelsError as error:
                out.append(f"  {label:<24} not configured" if "not configured" in str(error) else f"  {label:<24} invalid: {error}")
    return "\n".join(out)


def shell(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
