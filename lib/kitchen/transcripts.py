"""`kitchen retro`: my own prompts from Claude Code and Codex transcripts, for the retro skill to read.
Principles: `encode-lessons` (find the repeated corrections), `truthful-state` (a count built on automated messages is fake).

A retro counts interventions, so every message is classified by provenance first: who or what typed it.
Signals come from the transcript formats themselves, not from guessing at wording:
  Claude  origin.kind / promptSource (notifications), entrypoint sdk-cli (`claude -p`), assistant
          turns with a synthetic requestId (replayed history), and sessions whose first prompt an agent
          launched through `claude -p` or a T3 Code launch tool (found in that agent's own tool calls).
  Codex   session_meta originator/source/thread_source (exec, subagents, guardian reviews), user items
          without a client_id that carry the <heartbeat> envelope of a Codex automation, and messages a
          Claude agent sent into the thread through a T3 Code tool (found in that agent's own tool calls).
A few envelopes have no structured marker (harness injections, T3 Code notices); those
are matched by their fixed machine-written prefix.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import importlib.util

from .common import home, parse_ts, private_terms
from .credentials import redact

T3_CONTEXT = re.compile(r"<t3_context.*?</t3_context>", re.S)
HARNESS = (
    "<task-notification", "<local-command", "<system-reminder",
    "This session is being continued", "[Request interrupted",
    "Context handoff (",  # T3 Code injects these when it moves context between threads
    "<artifact-content-authored-by-others/>",  # leads a compaction summary that restates artifacts others wrote
)
DELEGATED = re.compile(r"^Act as the [\w -]+ sub-agent for this task\.")  # T3 Code wraps delegated tasks this way
# T3 Code's own notices: a delegated task's state or result (its machine id leads), a watched pull request's update,
# background work a server restart cancelled
T3_NOTICE = re.compile(r"^(?:Delegated tasks? \S.*? reached (?:a )?terminal states?\."
                       r"|Delegated task node:delegated-task:\S+ "
                       r"|Update on pull request #\d+ \(\S+\), which T3 Code is watching for you:"
                       r"|Note: the T3 server restarted, and this background work was cancelled)")
CODEX_REQUEST = re.compile(r"## My request(?: for Codex)?:\s*\n(.*)", re.S)
SLASH_COMMAND = re.compile(r"^<command-(?:name|message)>")  # Claude Code's envelope for a slash command I typed
COMMAND_NAME = re.compile(r"<command-name>([^<]*)</command-name>")
COMMAND_ARGS = re.compile(r"<command-args>([^<]*)</command-args>")
HEARTBEAT = re.compile(r"^<heartbeat>\s*<automation_id>([^<]+)</automation_id>")
LAUNCH = re.compile(r"\bclaude\b[^\n|;&]*\s(?:-p|--print)\b|\bcodex\s+exec\b")
# T3 Code tools that start a session with a prompt an agent wrote: the new thread's first message looks typed
# (promptSource "sdk", entrypoint "sdk-ts", no origin), exactly like a prompt the owner typed in T3.
T3_LAUNCH = re.compile(r'"name":\s*"mcp__t3[-_]code__(?:t3_thread_launch|create_threads|delegate_task|t3_thread_send)"')
T3_TEXT_KEYS = ("message", "task", "prompt")
CODEX_NOT_HUMAN_THREADS = {"subagent", "guardian_review", "security_scan"}

# Reasons a message is not counted as mine. Order is the order of the coverage line.
REASONS = ("notification", "harness", "subagent", "automation", "agent-launched", "non-interactive", "replayed")
UNKNOWN = "unknown"  # not counted as mine nor as excluded: the evidence is missing


def scrub(text: str) -> str:
    """Credentials out, then the private terms from the denylist (when there is one)."""
    text = redact(text)
    for pattern in private_terms() or []:
        text = pattern.sub("[PRIVATE]", text)
    return text


def prefix_key(text: str) -> str:
    return " ".join(text.split())[:60]


def _text_of(content) -> list[str]:
    if isinstance(content, list):
        return [part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"]
    return [content or ""]


def prompt_text(raw: str) -> str:
    text = T3_CONTEXT.sub("", raw).strip()
    return slash_command(text) if SLASH_COMMAND.match(text) else text


def slash_command(text: str) -> str:
    """`<command-name>/reload-skills</command-name>...` becomes `/reload-skills`, with its arguments."""
    name, args = COMMAND_NAME.search(text), COMMAND_ARGS.search(text)
    return f"{name.group(1) if name else ''} {args.group(1) if args else ''}".strip() or text


LAUNCH_WINDOW = datetime.timedelta(minutes=10)  # a launched session starts right after its launch
_SHELLPARSE = None


def shellparse():
    """The guards' shell parser (hooks/shellparse.py): which commands a Bash call actually runs."""
    global _SHELLPARSE
    if _SHELLPARSE is None:
        path = Path(__file__).resolve().parents[2] / "hooks" / "shellparse.py"
        spec = importlib.util.spec_from_file_location("kitchen_shellparse", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module  # its dataclasses look themselves up there
        spec.loader.exec_module(module)
        _SHELLPARSE = module
    return _SHELLPARSE


def launched(command: str, cwd: str | None) -> list[dict]:
    """The `claude -p` / `codex exec` calls a Bash command really runs, with the directory each runs in. A command
    that only mentions one (a heredoc, an echo, a string in a script) launches nothing. A command the parser cannot
    read is kept as one launch of unknown directory, so it can only make a session `unknown`, never `agent-launched`."""
    sp = shellparse()
    try:
        calls = sp.commands(command, cwd)
    except (sp.ParseError, RecursionError, ValueError):
        return [{"command": command, "dir": None}]
    out = []
    for call in calls:
        argv = [a.value for a in call.argv]
        if (call.name == "claude" and any(a in ("-p", "--print") for a in argv[1:])) or (call.name == "codex" and argv[1:2] == ["exec"]):
            # each argument quoted, so a short prompt still matches only as a whole argument (see matches_launch)
            quoted = " ".join('"' + " ".join(a.split()) + '"' for a in argv)
            out.append({"command": quoted, "dir": os.path.realpath(call.cwd) if call.cwd else None})
    return out
def agent_launches(since) -> list[dict]:
    """`claude -p` / `codex exec` commands agents ran: the command, the directory it ran in, and when."""
    launches = []
    root = home() / ".claude" / "projects"
    for transcript in sorted(root.glob("*/*.jsonl")):
        if transcript.stat().st_mtime < since.timestamp():
            continue
        for line in transcript.open(encoding="utf-8", errors="replace"):
            if '"tool_use"' not in line or not (LAUNCH.search(line) or T3_LAUNCH.search(line)):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            for part in event.get("message", {}).get("content", []) if event.get("type") == "assistant" else []:
                if not (isinstance(part, dict) and part.get("type") == "tool_use"):
                    continue
                ts = parse_ts(event.get("timestamp") or "")
                if T3_LAUNCH.search(json.dumps({"name": part.get("name")}, separators=(",", ":"))):
                    # the new thread's directory is chosen by T3 (often a fresh worktree), so it cannot be matched
                    for text in t3_texts(part.get("input")):
                        launches.append({"command": " ".join(text.split()), "dir": None, "ts": ts, "any_dir": True})
                    continue
                command = str((part.get("input") or {}).get("command", ""))
                if LAUNCH.search(command):
                    launches += [{**found, "ts": ts} for found in launched(command, event.get("cwd"))]
    return launches


def t3_texts(value) -> list[str]:
    """Every prompt in a T3 launch tool's input: `message`, `task` or `prompt`, at any depth (create_threads sends several)."""
    if isinstance(value, dict):
        found = [v for k, v in value.items() if k in T3_TEXT_KEYS and isinstance(v, str)]
        return found + [t for k, v in value.items() if k not in T3_TEXT_KEYS for t in t3_texts(v)]
    if isinstance(value, list):
        return [t for v in value for t in t3_texts(v)]
    return []


def matches_launch(prompt: str | None, command: str) -> bool:
    key = prefix_key(prompt or "")
    if not key:
        return False
    if len(key) >= 20:
        return key in command
    return f'"{key}"' in command or f"'{key}'" in command  # short prompts must appear quoted


def launch_evidence(first_prompt: str | None, cwd: str | None, started, launches: list[dict]) -> str | None:
    """"linked" when an agent launched this session: same text, run in this session's directory, just before it
    started. "unlinked" when only the text matches (another directory, another time): not enough to decide."""
    candidates = [launch for launch in launches if not launch.get("any_dir") and matches_launch(first_prompt, launch["command"])]
    if not candidates:
        return None
    here = os.path.realpath(cwd) if cwd else None
    for launch in candidates:
        if here and launch["dir"] == here and launch["ts"] and started and launch["ts"] <= started <= launch["ts"] + LAUNCH_WINDOW:
            return "linked"
    return "unlinked"


def t3_sent(text: str, ts, launches: list[dict]) -> bool:
    """An agent sent this very message through a T3 tool just before it arrived. Per message, not per session:
    the owner keeps typing in a thread an agent opened, and those messages carry the same fields."""
    return any(launch.get("any_dir") and launch["ts"] and ts and launch["ts"] <= ts <= launch["ts"] + LAUNCH_WINDOW
               and matches_launch(text, launch["command"]) for launch in launches)


def claude_prompts(since, launches: list[str]) -> list[dict]:
    root = home() / ".claude" / "projects"
    prompts = []
    for transcript in sorted(root.glob("*/*.jsonl")):  # main sessions only; subagent transcripts live deeper
        if transcript.stat().st_mtime < since.timestamp():
            continue
        events = []
        for line in transcript.open(encoding="utf-8", errors="replace"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        synthetic_parents = {
            e.get("parentUuid") for e in events
            if e.get("type") == "assistant" and str(e.get("requestId", "")).startswith("req_syn_")
        }
        first_prompt = next(
            (e.get("content") for e in events if e.get("type") == "queue-operation" and e.get("operation") == "enqueue"),
            next((prompt_text(t) for e in events if e.get("type") == "user" and not e.get("isMeta")
                  for t in _text_of(e.get("message", {}).get("content")) if prompt_text(t)), None),
        )
        session_cwd = next((e.get("cwd") for e in events if e.get("type") == "user" and e.get("cwd")), None)
        started = next((ts for e in events if e.get("type") in ("user", "queue-operation") for ts in [parse_ts(e.get("timestamp") or "")] if ts), None)
        launch = launch_evidence(first_prompt, session_cwd, started, launches)
        for event in events:
            if event.get("type") != "user" or event.get("isMeta"):
                continue
            ts = parse_ts(event.get("timestamp") or "")
            if ts is None or ts < since:
                continue
            content = event.get("message", {}).get("content")
            if isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content):
                continue
            origin = event.get("origin") if isinstance(event.get("origin"), dict) else {}
            marked_human = origin.get("kind") == "human" or event.get("promptSource") == "typed"
            for raw in _text_of(content):
                text = prompt_text(raw)
                if not text:
                    continue
                if origin.get("kind", "human") != "human" or event.get("promptSource") == "system" or (T3_NOTICE.match(text) and not marked_human):
                    reason = "notification"
                elif text.startswith(HARNESS):
                    reason = "harness"
                elif DELEGATED.match(text):
                    reason = "subagent"
                elif event.get("entrypoint") == "sdk-cli":
                    reason = "non-interactive"
                elif t3_sent(text, ts, launches):
                    reason = "agent-launched"
                elif event.get("uuid") in synthetic_parents:
                    reason = "replayed"
                elif origin.get("kind") == "human" or event.get("promptSource") == "typed":
                    reason = None  # the harness recorded a human turn in this session
                elif launch == "linked":
                    reason = "agent-launched"
                elif launch == "unlinked" and prefix_key(text) == prefix_key(first_prompt or ""):
                    reason = "unknown"  # an agent sent this text elsewhere; nothing in this session settles it
                else:
                    reason = None
                prompts.append({"ts": ts.isoformat(timespec="seconds"), "tool": "claude", "project": event.get("cwd") or transcript.parent.name,
                                "session": transcript.stem, "text": scrub(text), "excluded": reason})
    return prompts


def codex_session_reason(meta: dict) -> str | None:
    if not isinstance(meta.get("source"), str) or meta.get("thread_source") in CODEX_NOT_HUMAN_THREADS:
        return "subagent"  # guardian reviews and spawned subagents carry an object source or a subagent thread
    if meta.get("originator") == "codex_exec":
        return "non-interactive"
    return None


def codex_prompts(since, launches: list[dict]) -> list[dict]:
    root = home() / ".codex"
    files = list((root / "sessions").glob("*/*/*/rollout-*.jsonl")) + list((root / "archived_sessions").glob("rollout-*.jsonl"))
    automation_prompts = []
    for manifest in (root / "automations").glob("*/automation.toml"):
        match = re.search(r'^prompt\s*=\s*"(.{20,80})', manifest.read_text(encoding="utf-8"), re.MULTILINE)
        if match:
            automation_prompts.append(match.group(1)[:60])
    prompts = []
    for rollout in sorted(files):
        if rollout.stat().st_mtime < since.timestamp():
            continue
        cwd = session = None
        session_reason = None
        with rollout.open("rb") as handle:
            for raw in handle:
                head = raw[:300]
                if b'"type":"session_meta"' in head and session is None:
                    meta = json.loads(raw)["payload"]
                    cwd, session = meta.get("cwd"), meta.get("id")
                    session_reason = codex_session_reason(meta)
                elif b'"item_completed"' in head and b'"UserMessage"' in raw[:900]:
                    event = json.loads(raw)
                    ts = parse_ts(event.get("timestamp") or "")
                    if ts is None or ts < since:
                        continue
                    item = event["payload"]["item"]
                    text = "\n".join(part.get("text", "") for part in item.get("content", []) if part.get("type") == "text")
                    request = CODEX_REQUEST.search(text)
                    text = (request.group(1) if request else text).strip()
                    if not text:
                        continue
                    if session_reason:
                        reason = session_reason
                    elif "client_id" not in item and HEARTBEAT.match(text):
                        reason = "automation"  # a Codex heartbeat automation posting into this thread
                    elif DELEGATED.match(text):
                        reason = "subagent"
                    elif T3_NOTICE.match(text):
                        reason = "notification"  # T3 posts these into Codex threads with the same fields as a typed prompt
                    elif t3_sent(text, ts, launches):
                        reason = "agent-launched"  # a lead in another session sent it with t3_thread_send
                    elif any(prompt in text for prompt in automation_prompts):
                        reason = "automation"
                    else:
                        reason = None
                    prompts.append({"ts": ts.isoformat(timespec="seconds"), "tool": "codex", "project": cwd, "session": session,
                                    "text": scrub(text), "excluded": reason})
    return prompts


def collect(since, tool: str, pattern: str | None, include_automated: bool = False) -> tuple[list[dict], dict]:
    """Prompts to show, and coverage: how many were included and excluded, by reason."""
    found = []
    launches = agent_launches(since)
    if tool in ("all", "claude"):
        found += claude_prompts(since, launches)
    if tool in ("all", "codex"):
        found += codex_prompts(since, launches)
    if pattern:
        regex = re.compile(pattern, re.IGNORECASE)
        found = [p for p in found if regex.search(p["text"])]
    excluded = Counter(p["excluded"] for p in found if p["excluded"])
    coverage = {"included": sum(1 for p in found if not p["excluded"]), "excluded": {r: excluded[r] for r in REASONS if excluded[r]},
                "unknown": excluded[UNKNOWN]}
    shown = found if include_automated else [p for p in found if not p["excluded"]]
    if not include_automated:
        for p in shown:
            del p["excluded"]
    return sorted(shown, key=lambda p: p["ts"]), coverage


def coverage_line(coverage: dict) -> str:
    reasons = ", ".join(f"{reason} {count}" for reason, count in coverage["excluded"].items()) or "none"
    return (f"{coverage['included']} prompts included · {sum(coverage['excluded'].values())} excluded ({reasons})"
            f" · {coverage['unknown']} unknown provenance")


def render(prompts: list[dict], full: bool, coverage: dict) -> str:
    lines = []
    for p in prompts:
        text = p["text"] if full else p["text"].replace("\n", " ⏎ ")[:300]
        project = Path(p["project"]).name if p["project"] else "?"
        tag = f"[{p['excluded']}] " if p.get("excluded") else ""
        lines.append(f"{p['ts'][:16]}  {p['tool']:<6} {project:<24} {tag}{text}")
    lines.append(coverage_line(coverage))
    return "\n".join(lines)
