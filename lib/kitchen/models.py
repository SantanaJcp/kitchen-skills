"""`kitchen models`: which models do each role, in each person's own setup (~/.config/kitchen/models.toml).

Two roles, each read by a workflow. `reviewer`, the one `second-opinion` reads, depends on who wrote the work, so it is
set per author family (`--author claude` means the work is Claude's and its reviewers run another family's model). It
is a list: `set` replaces it with one model, `add` appends another, and second-opinion runs every lens on each, in
parallel. `worker`, the one chef-mode's Delegation reads for every delegated worker, is one model for any author, plus
an optional ordered list of accounts (T3 provider instance ids): `get worker --job` adjusts its effort to the job, its
tier to `--urgent`, and picks `accounts[index % len]`, so a batch alternates accounts with no state. Every entry is
explicit: provider (the CLI, or T3's provider of that name), model (`default` = that tool's own default), effort and
service tier.

A reviewer nobody set is an error with the command that sets it, never a default model; a reviewer running the author's
own family is refused, and so is a pi model whose family kitchen cannot tell. The one exception is the worker's model
(owner, 2026-10-29): a worker nobody set runs on the session model, and `get worker` says so on stderr with the command
that sets it. Whether the model exists is learned when it is used: the tool's error is shown as it is. pi alone has no
such error (it runs the nearest model it knows), so its call is refused unless `pi --list-models` lists the id exactly.
Principles: `cross-review` (another model reviews), `no-fallbacks` (no stand-in reviewer; the worker's fallback is said
aloud), `owner-attention` (set once).
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
ROLES = ("reviewer", "worker")
PER_AUTHOR = ("reviewer",)  # set per author family; the others hold one entry for any author
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TIERS = ("standard", "fast")
FIELDS = ("provider", "model", "effort", "tier")
JOBS = {"routine": -2, "judgment": -1, "hardest": 1}  # effort steps from the worker's configured one
T3_DRIVERS = {"claude": "claudeAgent", "codex": "codex", "pi": "pi"}  # T3's driverKind for each provider
WORKER_TOOLS = "Bash,Read,Edit,Write,Grep,Glob"
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


def key(role: str, author: str | None) -> tuple[str, str | None]:
    if role not in ROLES:
        raise ModelsError(f"unknown role {role!r}: one of {', '.join(ROLES)}")
    if role not in PER_AUTHOR:
        if author is not None:
            raise ModelsError(f"`{role}` is one model for any author: drop --author")
        return (role, None)
    if author not in AUTHORS:
        raise ModelsError(f"`{role}` depends on who wrote the work: pass --author {' or --author '.join(AUTHORS)}")
    return (role, author)


def set_command(role: str, author: str | None) -> str:
    who = f" --author {author}" if author else ""
    accounts = " [--accounts <t3-instance-id,...>]" if role not in PER_AUTHOR else ""
    return f"kitchen models set {role}{who} --provider <{'|'.join(PROVIDERS)}> --model <id|default> --effort <{'|'.join(EFFORTS)}> --tier <{'|'.join(TIERS)}>{accounts}"


def label(role: str, author: str | None) -> str:
    return f"{role} for {author} work" if author else role


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
        raise ModelsError(f"{label(role, author)}: each entry is a table of {', '.join(FIELDS)}; run `{set_command(role, author)}`")
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
    if role not in PER_AUTHOR:
        if entry["tier"] == "fast":
            raise ModelsError(f"a {role} runs fast only when `kitchen models get {role} --urgent` asks for it; set --tier standard")
        accounts = entry.get("accounts", [])
        if not isinstance(accounts, list) or not all(isinstance(a, str) and a.strip() == a and a for a in accounts):
            raise ModelsError(f"{role}: accounts is a list of T3 provider instance ids; run `{set_command(role, author)}`")
        if len(set(accounts)) < len(accounts):
            raise ModelsError(f"{role}: an account is listed twice")
        return {f: entry[f] for f in FIELDS} | ({"accounts": accounts} if accounts else {})
    if family(entry) == author:
        raise ModelsError(f"a {role} for {author}'s work must run another family's model: the author's model does not check its own work")
    return {f: entry[f] for f in FIELDS}


def raw(data: dict, role: str, author: str | None) -> list:
    if not isinstance(data.get(role, {}), dict):
        raise ModelsError(f"{path()}: {role} is not a table; fix it or rerun `{set_command(role, author)}`")
    if role not in PER_AUTHOR:
        return [data[role]] if role in data else []
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
    slot = key(role, author)
    if role not in PER_AUTHOR and len(entries) > 1:
        raise ModelsError(f"`{role}` is one model: `kitchen models set {role}` replaces it")
    clean = [validate(role, author, e) for e in entries]
    pairs = [(e["provider"], e["model"]) for e in clean]
    if len(set(pairs)) < len(pairs):
        raise ModelsError(f"{label(role, author)} already runs that provider and model; `kitchen models remove` it first to change it")
    data = load()
    unknown = sorted(set(data) - set(ROLES)) + sorted(f"{r}.{a}" for r in PER_AUTHOR if isinstance(data.get(r), dict) for a in set(data[r]) - set(AUTHORS))
    if unknown:  # rewriting the file would drop them silently
        raise ModelsError(f"{path()} has tables kitchen does not read ({', '.join(unknown)}); remove them by hand, then rerun")
    slots = [(r, a) for r in ROLES for a in (AUTHORS if r in PER_AUTHOR else (None,))]
    lists = {(r, a): [validate(r, a, e) for e in raw(data, r, a)] for r, a in slots}
    lists[slot] = clean
    lines = ["# kitchen models: which models do each role. Written by `kitchen models set|add|remove`; each person sets their own.", ""]
    for (role_name, a), values in lists.items():
        for value in values:
            lines.append(f"[[{role_name}.{a}]]" if a else f"[{role_name}]")
            lines += [f"{f} = {json.dumps(value[f])}" for f in (*FIELDS, "accounts") if f in value]
            lines.append("")
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


def remove(role: str, author: str | None, provider: str, model: str | None) -> None:
    entries = get(role, author)
    matches = [e for e in entries if e["provider"] == provider and model in (None, e["model"])]
    if not matches:
        raise ModelsError(f"no {label(role, author)} runs on {provider}{' ' + model if model else ''}")
    if len(matches) > 1:
        raise ModelsError(f"more than one {label(role, author)} runs on {provider}: pass --model")
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


def command(entry: dict, writes: bool = False) -> list[str]:
    """The non-interactive CLI call for a role outside T3: `codex exec`, `claude -p` or `pi -p`. The prompt goes last.
    A reviewer reads only; a worker (`writes`) edits and runs commands without asking: codex in its workspace-write
    sandbox, claude with edits accepted and the tools it needs allowed, pi with its default tools."""
    if entry["provider"] == "codex":
        argv = ["codex", "exec", "--skip-git-repo-check", "-s", "workspace-write" if writes else "read-only"]
        if entry["model"] != "default":
            argv += ["-m", entry["model"]]
        return argv + ["-c", f'model_reasoning_effort="{entry["effort"]}"',
                       "-c", f'service_tier="{"priority" if entry["tier"] == "fast" else "default"}"']
    if entry["provider"] == "pi":  # pi has no sandbox: read-only is its read tools alone, no shell and no MCP
        pi_lists(entry["model"])
        tools = [] if writes else ["--no-mcp", "--tools", PI_READ_ONLY]
        return ["pi", "-p", "--no-session", *tools, "--model", entry["model"], "--thinking", entry["effort"]]
    if entry["tier"] == "fast":
        raise ModelsError("the claude CLI has no flag for the fast tier; set --tier standard, or run the role where the tier can be set (T3's delegate_task)")
    argv = ["claude", "-p"] + (["--allowedTools", WORKER_TOOLS] if writes else [])  # variadic: never last, or it takes the prompt
    if entry["model"] != "default":
        argv += ["--model", entry["model"]]
    return argv + ["--effort", entry["effort"]] + (["--permission-mode", "acceptEdits"] if writes else [])


def worker(job: str, urgent: bool, index: int) -> dict | None:
    """The worker for one job: effort moved from the configured one by the job's steps, between low and max; the fast
    tier only when urgent; the account at `index` in the configured order. None when the worker is not set."""
    if job not in JOBS:
        raise ModelsError(f"--job is one of {', '.join(JOBS)}, not {job!r}")
    if index < 0:
        raise ModelsError("--index counts from 0")
    entries = current("worker", None)
    if not entries:
        return None
    entry = entries[0]
    step = min(max(EFFORTS.index(entry["effort"]) + JOBS[job], 0), len(EFFORTS) - 1)
    if urgent and entry["provider"] == "pi":
        raise ModelsError("pi has no service tier, in its CLI or in T3, so the worker cannot run fast: drop --urgent or set another provider")
    out = {f: entry[f] for f in FIELDS} | {"effort": EFFORTS[step], "tier": "fast" if urgent else "standard"}
    accounts = entry.get("accounts", [])
    return out | ({"account": accounts[index % len(accounts)]} if accounts else {})


def unset_worker() -> str:
    return (f"worker is not configured in {path()}: the worker runs on the session model; "
            f"set it with `{set_command('worker', None)}`")


def t3(entry: dict) -> dict:
    """The `target` of T3's delegate_task: the account as providerInstanceId (the provider's driverKind when none is
    set), the model (left out for `default`), and the effort and tier options each provider takes."""
    provider = entry["provider"]
    target = {"providerInstanceId": entry["account"]} if entry.get("account") else {"driverKind": T3_DRIVERS[provider]}
    if entry["model"] != "default":
        target["model"] = entry["model"]
    fast = entry["tier"] == "fast"
    target["options"] = {"claude": {"effort": entry["effort"], "fastMode": fast},
                         "codex": {"reasoningEffort": entry["effort"], "serviceTier": "priority" if fast else "default"},
                         "pi": {"thinking": entry["effort"]}}[provider]
    return target


def describe(entry: dict) -> str:
    accounts = f" · accounts {', '.join(entry['accounts'])}" if entry.get("accounts") else ""
    account = f" · account {entry['account']}" if entry.get("account") else ""
    return f"{entry['provider']} {entry['model']} · effort {entry['effort']} · tier {entry['tier']}{accounts}{account}"


def render() -> str:
    out = [f"kitchen models  ({path()})"]
    try:
        entries = current("worker", None)
        out.append(f"  {'worker':<24} {describe(entries[0])}" if entries else f"  {'worker':<24} not configured: runs on the session model")
    except ModelsError as error:
        out.append(f"  {'worker':<24} invalid: {error}")
    for role in PER_AUTHOR:
        for author in AUTHORS:
            name = label(role, author)
            try:
                for i, e in enumerate(get(role, author)):
                    out.append(f"  {name if i == 0 else '':<24} {describe(e)}")
            except ModelsError as error:
                out.append(f"  {name:<24} not configured" if "not configured" in str(error) else f"  {name:<24} invalid: {error}")
    return "\n".join(out)


def shell(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
