"""`kitchen models`: which models each role runs, in each person's own setup (~/.config/kitchen/models.toml).

Two roles. `reviewer` is the one `second-opinion` reads. It depends on who wrote the work, so it is set per author
family (`--author claude` means the work is Claude's and its reviewers run another family's model). A reviewer role is
a list: `set` replaces it with one model, `add` appends another, and second-opinion runs every lens on each, in
parallel. `worker` is the one model that runs delegated execution and exploration, whoever wrote the work and whatever
its family. It holds one entry: set once globally, with an optional override per project. A project is a git repo,
named by its main worktree, so every worktree and subdirectory of a repo shares one override. A new role is added when
a workflow reads it, not before. Every entry is explicit: provider (the CLI, or T3's provider of that name), model
(`default` = that tool's own default), effort and service tier.

Storage: `[[reviewer.<author>]]` lists, `[worker]` for the global entry and `[worker.projects."<main worktree>"]` for
each override. A table kitchen does not read is refused by a write, never dropped.

A role nobody set is an error with the command that sets it, never a default model; a reviewer running the author's
own family is refused, and so is a pi model whose family kitchen cannot tell. Whether the model exists is learned when
it is used: the tool's error is shown as it is. pi alone has no such error (it runs the nearest model it knows), so its
call is refused unless `pi --list-models` lists the id exactly.
Principles: `cross-review` (another model reviews), `no-fallbacks` (no stand-in model), `owner-attention` (set once).
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import tomllib
from pathlib import Path

from kitchen import common

AUTHORS = ("claude", "codex")  # who wrote the work
PROVIDERS = ("claude", "codex", "pi")  # what runs a role
ROLES = ("reviewer", "worker")  # reviewer is set per author family; worker is one entry, global or per project
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


def label(role: str, author: str) -> str:
    return f"{role} for {author} work"


def set_command(role: str, author: str | None = None) -> str:
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
    who = f" for {author}" if author else ""
    if not isinstance(entry, dict):
        raise ModelsError(f"{role}{who}: each entry is a table of {', '.join(FIELDS)}; run `{set_command(role, author)}`")
    missing = [f for f in FIELDS if not isinstance(entry.get(f), str) or not entry.get(f)]
    if missing:
        raise ModelsError(f"{role}{who}: missing {', '.join(missing)}; run `{set_command(role, author)}`")
    if entry["provider"] not in PROVIDERS:
        raise ModelsError(f"provider must be one of {', '.join(PROVIDERS)}, not {entry['provider']!r}")
    if entry["effort"] not in EFFORTS:
        raise ModelsError(f"effort must be one of {', '.join(EFFORTS)}, not {entry['effort']!r}")
    if entry["tier"] not in TIERS:
        raise ModelsError(f"tier must be one of {', '.join(TIERS)}, not {entry['tier']!r}")
    if entry["provider"] == "pi" and entry["tier"] == "fast":
        raise ModelsError("pi has no service tier, in its CLI or in T3; set --tier standard")
    if role == "reviewer" and family(entry) == author:
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


def worker_tables(data: dict) -> tuple[dict | None, dict]:
    """The [worker] table: the global entry (None when none of its fields is set) and the overrides by project path."""
    node = data.get("worker")
    if node is None:
        return None, {}
    if not isinstance(node, dict):
        raise ModelsError(f"{path()}: worker is not a table; fix it or rerun `{set_command('worker')}`")
    projects = node.get("projects", {})
    if not isinstance(projects, dict) or not all(isinstance(e, dict) for e in projects.values()):
        raise ModelsError(f"{path()}: worker.projects is not a table of project tables; fix it by hand")
    return {f: node[f] for f in FIELDS if f in node} or None, projects


def read_file() -> dict:
    """Every table the file holds, validated, for a rewrite. A table kitchen does not read is refused, never dropped."""
    data = load()
    unknown = sorted(set(data) - set(ROLES))
    if isinstance(data.get("reviewer"), dict):
        unknown += sorted(f"reviewer.{a}" for a in set(data["reviewer"]) - set(AUTHORS))
    if isinstance(data.get("worker"), dict):
        unknown += sorted(f"worker.{k}" for k in set(data["worker"]) - set(FIELDS) - {"projects"})
    if unknown:  # rewriting the file would drop them silently
        raise ModelsError(f"{path()} has tables kitchen does not read ({', '.join(unknown)}); remove them by hand, then rerun")
    global_entry, projects = worker_tables(data)
    return {
        "reviewer": {a: [validate("reviewer", a, e) for e in raw(data, "reviewer", a)] for a in AUTHORS},
        "worker": {"global": None if global_entry is None else validate("worker", None, global_entry),
                   "projects": {p: validate("worker", None, e) for p, e in projects.items()}},
    }


TOML_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def toml_string(text: str) -> str:
    """A TOML basic string: the quote, the backslash and the control characters are escaped, every other character is
    written as it is. json.dumps would write an emoji as a surrogate pair, which TOML rejects."""
    out = []
    for char in text:
        code = ord(char)
        if 0xD800 <= code <= 0xDFFF:  # a lone surrogate (a path that is not UTF-8) is not text TOML can hold
            raise ModelsError(f"cannot store {text!r}: it is not valid Unicode text")
        if char in TOML_ESCAPES:
            out.append(TOML_ESCAPES[char])
        elif code < 0x20 or code == 0x7F:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def table(header: str, value: dict) -> list[str]:
    return [header, *(f"{f} = {toml_string(value[f])}" for f in FIELDS), ""]


def save(state: dict) -> None:
    lines = ["# kitchen models: which models do each role. Written by `kitchen models set|add|remove`; each person sets their own.", ""]
    for author in AUTHORS:
        for value in state["reviewer"][author]:
            lines += table(f"[[reviewer.{author}]]", value)
    if state["worker"]["global"] is not None:
        lines += table("[worker]", state["worker"]["global"])
    for project, value in sorted(state["worker"]["projects"].items()):
        lines += table(f"[worker.projects.{toml_string(project)}]", value)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines), encoding="utf-8")


def check_scope(role: str, author: str | None, project: str | None) -> None:
    """Each role's own scope: a reviewer is set per author family, a worker is one entry, global or per project."""
    if role == "worker":
        if author is not None:
            raise ModelsError("`worker` does not depend on who wrote the work: drop --author; one worker runs every author's work")
        return
    if author not in AUTHORS:
        raise ModelsError(f"`{role}` depends on who wrote the work: pass --author {' or --author '.join(AUTHORS)}")
    if project is not None:
        raise ModelsError(f"`{role}` is set per author, not per project: drop --project")


def usage_problem(role: str, action: str, author: str | None, provider: str | None) -> str | None:
    """A reviewer call missing the flag that names its scope or its model is a usage error: the parser reports it, exit 2."""
    if role != "reviewer":
        return None
    if author is None:
        return f"`{role}` depends on who wrote the work: pass --author {' or --author '.join(AUTHORS)}"
    if action == "remove" and provider is None:
        return f"remove needs --provider for {label(role, author)}"
    return None


def current(role: str, author: str) -> list[dict]:
    return [validate(role, author, e) for e in raw(load(), role, author)]


def get(role: str, author: str) -> list[dict]:
    """A reviewer role's models; fails, with the command that sets it, when none is configured."""
    entries = current(role, author)
    if not entries:
        raise ModelsError(f"{label(role, author)} is not configured in {path()}: run `{set_command(role, author)}`")
    return entries


def put(role: str, author: str, entries: list[dict]) -> None:
    clean = [validate(role, author, e) for e in entries]
    pairs = [(e["provider"], e["model"]) for e in clean]
    if len(set(pairs)) < len(pairs):
        raise ModelsError(f"{label(role, author)} already runs that provider and model; `kitchen models remove` it first to change it")
    state = read_file()
    state["reviewer"][author] = clean
    save(state)


# git words the end of its search by filesystem layout ("of the parent directories", or "up to mount point /" when the
# search stops at a device boundary), and both mean that nothing was found. A broken `.git` names its gitdir instead.
OUTSIDE_A_REPO = "not a git repository (or any"


def git_in(directory: Path, *args: str) -> subprocess.CompletedProcess:
    # a git hook exports GIT_DIR and friends: DIR, not the hook's repo, must decide which project this is
    env = {**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, "LC_ALL": "C"}
    try:
        return subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True, env=env, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ModelsError(f"cannot run git in {directory} to name its project ({error})") from error


def git_text(directory: Path, *args: str) -> str:
    found = git_in(directory, *args)
    if found.returncode != 0:
        raise ModelsError(f"git could not name the project of {directory}: {found.stderr.strip()}")
    return found.stdout.strip()


def project_root(directory: Path) -> Path | None:
    """The project DIR belongs to: the main checkout of its git repo, shared by every worktree and subdirectory of it.
    git lists the main worktree first, and a bare repo is its own project. git lists a separate git dir (or a
    submodule's) by that git dir, not by its checkout: then the project is the checkout DIR is in, and a linked worktree
    of such a repo is refused, since the listing does not name its checkout. None only outside a repo: any other git
    failure is an error."""
    if not directory.is_dir():
        raise ModelsError(f"{directory} is not a directory")
    listed = git_in(directory, "worktree", "list", "--porcelain")
    if listed.returncode != 0:
        if OUTSIDE_A_REPO in listed.stderr:
            return None
        raise ModelsError(f"git could not name the project of {directory}: {listed.stderr.strip()}")
    lines = listed.stdout.split("\n\n", 1)[0].splitlines()
    if not lines or not lines[0].startswith("worktree "):
        raise ModelsError(f"git named no main worktree for {directory}: {listed.stdout.strip()!r}")
    main = Path(lines[0].removeprefix("worktree "))
    if "bare" in lines:
        return main
    if git_text(main, "rev-parse", "--is-inside-git-dir") == "false":  # a checkout
        return main
    if os.path.realpath(git_text(directory, "rev-parse", "--absolute-git-dir")) != os.path.realpath(main):
        raise ModelsError(f"{directory} is a linked worktree of {main}, a git dir (not a checkout): "
                          f"run it from the main checkout, which names the project")
    return Path(git_text(directory, "rev-parse", "--show-toplevel"))


def named_root(project: str) -> Path:
    root = project_root(Path(project))
    if root is None:
        raise ModelsError(f"{project} is not in a git repository: a worker override belongs to a project, and a project is a git repo")
    return root


def worker_label(root: Path | None) -> str:
    return "worker (global)" if root is None else f"worker (project {root})"


def set_worker(project: str | None, entry: dict) -> str:
    clean = validate("worker", None, entry)
    root = None if project is None else named_root(project)
    state = read_file()
    if root is None:
        state["worker"]["global"] = clean
    else:
        state["worker"]["projects"][str(root)] = clean
    save(state)
    return worker_label(root)


def removal_root(project: str) -> Path:
    """The key `remove worker --project` deletes: the repo DIR names while DIR exists, else the path as it was stored,
    so an override whose repo has moved away is removed by its old path."""
    if Path(project).exists():
        return named_root(project)
    return Path(os.path.abspath(project))


def remove_worker(project: str | None) -> str:
    root = None if project is None else removal_root(project)
    state = read_file()
    if root is None:
        if state["worker"]["global"] is None:
            raise ModelsError(f"no worker is set globally in {path()}")
        state["worker"]["global"] = None
    elif state["worker"]["projects"].pop(str(root), None) is None:
        raise ModelsError(f"no worker is set for {root} in {path()}")
    save(state)
    return worker_label(root)


def resolve_worker(project: str | None) -> tuple[str, dict]:
    """The worker that answers: this project's override, else the global entry. Without --project the project is the
    current directory's; outside a repo only the global entry can answer."""
    root = project_root(Path.cwd()) if project is None else named_root(project)
    global_entry, projects = worker_tables(load())
    if global_entry is not None:
        global_entry = validate("worker", None, global_entry)
    if root is not None and str(root) in projects:
        return "project", validate("worker", None, projects[str(root)])
    if global_entry is not None:
        return "global", global_entry
    raise ModelsError(f"worker is not configured in {path()}: run `{set_command('worker')}`, or the same with --project DIR for one repo")


def set_entry(role: str, author: str | None, project: str | None, entry: dict) -> str:
    check_scope(role, author, project)
    if role == "worker":
        return set_worker(project, entry)
    put(role, author, [entry])
    return label(role, author)


def add_entry(role: str, author: str | None, project: str | None, entry: dict) -> str:
    check_scope(role, author, project)
    if role == "worker":
        raise ModelsError("add is refused for worker: it holds one model; `kitchen models set worker` replaces it")
    put(role, author, current(role, author) + [entry])
    return label(role, author)


def remove_entry(role: str, author: str | None, project: str | None, provider: str | None, model: str | None) -> str:
    check_scope(role, author, project)
    if role == "worker":
        if provider is not None or model is not None:
            raise ModelsError("remove takes no --provider or --model for worker: it removes the entry of that scope (--project DIR, or the global one)")
        return remove_worker(project)
    if provider is None:
        raise ModelsError(f"remove needs --provider for {label(role, author)}")
    entries = get(role, author)
    matches = [e for e in entries if e["provider"] == provider and model in (None, e["model"])]
    if not matches:
        raise ModelsError(f"no {role} for {author} work runs on {provider}{' ' + model if model else ''}")
    if len(matches) > 1:
        raise ModelsError(f"more than one {role} for {author} work runs on {provider}: pass --model")
    put(role, author, [e for e in entries if e is not matches[0]])
    return label(role, author)


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


def show(role: str, author: str | None, project: str | None, call: bool, as_json: bool) -> str:
    """What `kitchen models get` prints. A reviewer prints its lines; a worker prints its one entry and the scope that answered."""
    check_scope(role, author, project)
    if role == "worker":
        if call:  # the CLI calls are a reviewer's, read-only; a worker that edits launched that way could not write
            raise ModelsError("--command prints a reviewer's read-only call; launch a worker through a route that runs its provider "
                              "(T3's delegate_task, or that provider's own CLI) with the model, effort and tier `kitchen models get worker` prints")
        scope, entry = resolve_worker(project)
        if as_json:
            return json.dumps({**entry, "scope": scope})
        return f"{describe(entry)} ({scope})"
    entries = get(role, author)
    if call:
        return "\n".join(shell(command(e)) for e in entries)
    return json.dumps(entries) if as_json else "\n".join(describe(e) for e in entries)


def worker_text(entry: dict | None) -> str:
    if entry is None:
        return "not configured"
    try:
        return describe(validate("worker", None, entry))
    except ModelsError as error:
        return f"invalid: {error}"


def missing_marker(project: str) -> str:
    """An override whose repo was moved or deleted keeps its key until it is removed by that path."""
    return "" if Path(project).exists() else "  (path missing)"


def render_worker() -> list[str]:
    try:
        global_entry, projects = worker_tables(load())
    except ModelsError as error:
        return [f"  {'worker (global)':<24} invalid: {error}"]
    lines = [f"  {'worker (global)':<24} {worker_text(global_entry)}"]
    lines += [f"  {'worker (project)':<24} {worker_text(entry)}  for {project}{missing_marker(project)}"
              for project, entry in sorted(projects.items())]
    return lines


def render() -> str:
    out = [f"kitchen models  ({path()})"]
    for author in AUTHORS:
        name = label("reviewer", author)
        try:
            for i, e in enumerate(get("reviewer", author)):
                out.append(f"  {name if i == 0 else '':<24} {describe(e)}")
        except ModelsError as error:
            out.append(f"  {name:<24} not configured" if "not configured" in str(error) else f"  {name:<24} invalid: {error}")
    out += render_worker()
    return "\n".join(out)


def shell(argv: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)
