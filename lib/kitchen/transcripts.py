"""`kitchen retro`: my own prompts from Claude Code and Codex transcripts, for the retro skill to read.
Principles: `encode-lessons` (find the repeated corrections), `truthful-state` (a count built on automated messages is fake).

A retro counts interventions, so every message is classified by provenance first: who or what typed it.
Signals come from the transcript formats themselves, not from guessing at wording:
  Claude  origin.kind / promptSource (notifications), entrypoint sdk-cli (`claude -p`), assistant
          turns with a synthetic requestId (replayed history), a uuid the same transcript already holds
          (a rewritten history: counted once), sessions whose first prompt an agent launched through
          `claude -p` or a T3 Code launch tool (found in that agent's own tool calls), and text equal to a
          prompt an agent gave T3 Code's scheduler at or before the message (successful schedule calls, and the
          scheduled_tasks rows of T3's state database).
  Codex   session_meta originator/source/thread_source (exec, subagents, guardian reviews), user items
          without a client_id that carry the <heartbeat> envelope of a Codex automation, and the full text of a
          message a T3 Code launch tool sent (the launch evidence of Claude's own tool calls; never for an item
          with a client_id), and successful schedule calls in Codex rollouts.
A few envelopes have no structured marker (harness injections, T3 Code notices and delegation results, the
review brief of a script an agent runs); those are matched by their fixed machine-written prefix, and each
rule names what writes its prefix. In Claude, explicit human evidence (origin.kind human, promptSource typed)
outranks those text rules.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

import importlib.util

from .common import home, parse_ts, private_terms
from .credentials import redact

T3_CONTEXT = re.compile(r"<t3_context.*?</t3_context>", re.S)
HARNESS = (
    "<task-notification", "<local-command", "<system-reminder",
    "This session is being continued",  # Claude Code's compaction summary
    "[Request interrupted",
    "Context handoff (",  # T3 Code injects these when it moves context between threads
)
# Text rules: in Claude they yield to explicit human evidence (see claude_prompts).
ARTIFACT_SUMMARY = "<artifact-content-authored-by-others/>"  # the compaction summary, when the conversation held Artifact content by others
RESUME = "Continue where you left off."  # T3 Code's CONTINUE_PROMPT, sent to resume a thread
DELEGATED = re.compile(r"^Act as the [\w -]+ sub-agent for this task\.")  # T3 Code wraps delegated tasks this way
DELEGATION_NOTICE = re.compile(r"^Delegated tasks? \S.*? reached (?:a )?terminal states?\.")  # T3 Code reports finished delegations
DELEGATED_RESULT = re.compile(r"^Delegated task \S+ (?:\(.*\) )?\[[a-z_-]+\]:\n")  # T3 Code posts a delegated task's result under this header
T3_NOTICE = re.compile(
    r"^Update on pull request #\d+ \(\S+\), which T3 Code is watching for you:"  # T3 Code: a watched pull request changed
    r"|^T3 Code stopped watching pull request #\d+ \("  # T3 Code: the host failed to read the pull request 8 times
    r"|^Note: the T3 server restarted, and this background work was cancelled"  # T3 Code: a restart cancelled background work
)
REVIEW_BRIEF = re.compile(
    r"^You are a reviewer for a code or documentation change\b"  # a review script an agent runs through Bash writes it into each session it opens; no T3 or `claude -p` call is visible for it
    r"|^You are the [\w+ -]+ reviewer for a (?:code change|design review|design\+diff review)\. Review only; do not edit files\."  # the kitchen's reviewer brief, one per lens (skills/second-opinion/reviewer-prompt.md)
)
SCHEDULE_TOOL = re.compile(r"mcp__t3[-_]code__(?:schedule_task|update_scheduled_task)")  # the agent's call that gives T3 Code a prompt to run on a schedule; matched against a tool name
MIN_SCHEDULED = 20  # a shorter scheduled prompt could equal an ordinary message, so it is never matched
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
REASONS = ("notification", "harness", "subagent", "automation", "agent-launched", "non-interactive", "replayed", "duplicate")
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
                    # by directory; a cwd or thread the call does name is checked by codex_launch
                    target = t3_target(part.get("input"))
                    for text in t3_texts(part.get("input")):
                        launches.append({"command": " ".join(text.split()), "dir": None, "ts": ts, "any_dir": True, **target})
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


def t3_target(value) -> dict:
    """Where a T3 launch call sends its message, when the call names it: a `cwd` or a `threadId` at its top level."""
    if not isinstance(value, dict):
        return {}
    target = {}
    if isinstance(value.get("cwd"), str):
        target["cwd"] = os.path.realpath(value["cwd"])
    if isinstance(value.get("threadId"), str):
        target["thread"] = value["threadId"]
    return target


def codex_launch(text: str, ts, cwd: str | None, session: str | None, launches: list[dict]) -> str | None:
    """How a T3 launch call relates to this Codex message. "linked": a call sent exactly this text just before it
    arrived, into the directory and thread that call named (when it named one). "unlinked": a call sent exactly this
    text, but into another directory or thread. None: no call sent this text. Codex text is never matched by prefix:
    two messages of different projects can share their first 60 characters."""
    norm = " ".join(text.split())
    sent = [launch for launch in launches if launch.get("any_dir") and launch["command"] == norm
            and launch["ts"] and ts and launch["ts"] <= ts <= launch["ts"] + LAUNCH_WINDOW]
    if not sent:
        return None
    here = os.path.realpath(cwd) if cwd else None
    for launch in sent:
        if ("cwd" not in launch or launch["cwd"] == here) and ("thread" not in launch or launch["thread"] == session):
            return "linked"
    return "unlinked"


class TranscriptsError(Exception):
    """A source `kitchen retro` cannot read. It fails loudly: a count made without that source would be a fake count."""


def _json_line(line: str | bytes) -> dict | None:
    try:
        event = json.loads(line)  # a line that is not JSON, or not UTF-8 (JSONDecodeError and UnicodeDecodeError are ValueErrors)
    except ValueError:
        return None
    return event if isinstance(event, dict) else None


def _content(event: dict | None, kind: str) -> list[dict]:
    """The dict parts of a Claude transcript event's message content, for events of one type."""
    if not event or event.get("type") != kind:
        return []
    content = (event.get("message") or {}).get("content")
    return [part for part in content if isinstance(part, dict)] if isinstance(content, list) else []


def t3_scheduled_rows() -> list[tuple[str, datetime.datetime]]:
    """Each scheduled_tasks row of T3's state database, with its creation time. Read only when the database exists;
    a database without that table or column is an error, not an empty list."""
    state = home() / ".t3" / "userdata" / "statev2.sqlite"
    if not state.exists():
        return []
    try:
        with closing(sqlite3.connect(f"{state.as_uri()}?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT prompt, created_at FROM scheduled_tasks").fetchall()
    except sqlite3.Error as error:
        raise _unreadable_t3(state, str(error)) from error
    out = []
    for prompt, created in rows:
        when = parse_ts(created) if isinstance(created, str) else None
        if when is None:
            raise _unreadable_t3(state, f"created_at {created!r} is not an ISO timestamp")
        out.append((prompt, when))
    return out


def _unreadable_t3(path: Path, detail: str) -> TranscriptsError:
    return TranscriptsError(f"retro: cannot read T3's scheduled tasks from {path}: {detail}; "
                            "without them scheduled prompts cannot be told from yours")


def schedules_a_task(answer) -> bool:
    """True when a scheduling tool's answer names the task it saved: T3 answers schedule_task and update_scheduled_task
    with the task as JSON, `scheduledTaskId` included, as a string or inside text parts. An error text, a refusal or an
    empty answer names none, so it is no evidence that anything was scheduled."""
    if isinstance(answer, str):
        try:
            answer = json.loads(answer)
        except (json.JSONDecodeError, TypeError):
            return False
    if isinstance(answer, list):
        return any(schedules_a_task(part) for part in answer)
    if isinstance(answer, dict):
        if isinstance(answer.get("scheduledTaskId"), str) and answer["scheduledTaskId"]:
            return True
        return any(schedules_a_task(answer.get(key)) for key in ("text", "content", "output"))
    return False


def claude_schedule_calls(transcript: Path) -> list[tuple[str, datetime.datetime]]:
    """(prompt, time) of this transcript's schedule_task / update_scheduled_task calls whose result names the task saved
    (schedules_a_task). The result is found by tool_use_id; a result with is_error, or one that names no task, scheduled
    nothing, and a call with no result is no evidence."""
    calls = {}  # tool_use id -> (prompt, time)
    with transcript.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if "schedule" not in line or '"tool_use"' not in line:
                continue
            event = _json_line(line)
            ts = parse_ts((event or {}).get("timestamp") or "")
            for part in _content(event, "assistant"):
                if part.get("type") == "tool_use" and isinstance(part.get("id"), str) and ts \
                        and SCHEDULE_TOOL.fullmatch(str(part.get("name", ""))):
                    calls[part["id"]] = ((part.get("input") or {}).get("prompt"), ts)
    if not calls:
        return []
    saved = {}  # tool_use id -> whether its result names the task saved
    with transcript.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if '"tool_result"' not in line or not any(call_id in line for call_id in calls):
                continue
            for part in _content(_json_line(line), "user"):
                if part.get("type") == "tool_result" and part.get("tool_use_id") in calls:
                    saved[part["tool_use_id"]] = not part.get("is_error") and schedules_a_task(part.get("content"))
    return [calls[call_id] for call_id in calls if saved.get(call_id)]


def codex_rollouts() -> list[Path]:
    root = home() / ".codex"
    return list((root / "sessions").glob("*/*/*/rollout-*.jsonl")) + list((root / "archived_sessions").glob("rollout-*.jsonl"))


def codex_schedule_calls(rollout: Path) -> list[tuple[str, datetime.datetime]]:
    """(prompt, time) of this rollout's schedule_task / update_scheduled_task calls whose output names the task saved
    (schedules_a_task). Codex records no error flag on a tool output, so the answer itself must show the task; an error
    text, or a call with no output, is no evidence."""
    calls = {}  # call_id -> (prompt, time)
    with rollout.open("rb") as handle:
        for raw in handle:
            if b"schedule" not in raw or b'"function_call"' not in raw:
                continue
            event = _json_line(raw)
            payload = (event or {}).get("payload") or {}
            if payload.get("type") != "function_call" or not isinstance(payload.get("call_id"), str) \
                    or not SCHEDULE_TOOL.fullmatch(str(payload.get("name", ""))):
                continue
            ts = parse_ts(event.get("timestamp") or "")
            try:
                arguments = json.loads(payload.get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if ts and isinstance(arguments, dict):
                calls[payload["call_id"]] = (arguments.get("prompt"), ts)
    if not calls:
        return []
    answered = set()
    with rollout.open("rb") as handle:
        for raw in handle:
            if b"function_call_output" not in raw or not any(call_id.encode() in raw for call_id in calls):
                continue
            payload = (_json_line(raw) or {}).get("payload") or {}
            if payload.get("type") == "function_call_output" and payload.get("call_id") in calls \
                    and schedules_a_task(payload.get("output")):
                answered.add(payload["call_id"])
    return [calls[call_id] for call_id in answered]


def scheduled_prompts() -> dict[str, datetime.datetime]:
    """Prompts an agent gave T3 Code's scheduler, whitespace-normalized, each with the earliest time it was scheduled.
    T3 posts each one verbatim, so a message is automated only when it equals one and arrives at or after that time.
    Three sources: the scheduled_tasks rows of T3's state database (creation time), and the schedule_task /
    update_scheduled_task calls that succeeded, in Claude and in Codex transcripts (call time). A deleted task leaves no
    row behind, so the calls are the only record of it."""
    found = {}

    def note(prompt, when):
        if isinstance(prompt, str) and when is not None:
            norm = " ".join(prompt.split())
            if len(norm) >= MIN_SCHEDULED and (norm not in found or when < found[norm]):
                found[norm] = when

    for prompt, when in t3_scheduled_rows():
        note(prompt, when)
    for transcript in sorted((home() / ".claude" / "projects").glob("*/*.jsonl")):
        for prompt, when in claude_schedule_calls(transcript):
            note(prompt, when)
    for rollout in codex_rollouts():
        for prompt, when in codex_schedule_calls(rollout):
            note(prompt, when)
    return found


def scheduled_at(norm: str, ts, scheduled: dict[str, datetime.datetime]) -> bool:
    """Whether an agent had scheduled this exact prompt by the time this message arrived."""
    when = scheduled.get(norm)
    return when is not None and ts is not None and ts >= when


def claude_prompts(since, launches: list[dict], scheduled: dict[str, datetime.datetime]) -> list[dict]:
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
        seen = set()  # (uuid, text) already in this transcript: a history rewritten on resume repeats a message under its uuid
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
            uuid = event.get("uuid")
            for raw in _text_of(content):
                text = prompt_text(raw)
                if not text:
                    continue
                norm = " ".join(text.split())
                copy = bool(uuid) and (uuid, norm) in seen
                seen.add((uuid, norm))
                if copy:
                    reason = "duplicate"
                elif origin.get("kind", "human") != "human" or event.get("promptSource") == "system" or DELEGATION_NOTICE.match(text):
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
                    reason = None  # the harness recorded a human turn in this session: it outranks the text rules below
                elif T3_NOTICE.match(text) or DELEGATED_RESULT.match(text):
                    reason = "notification"
                elif text.startswith(ARTIFACT_SUMMARY) or text == RESUME:
                    reason = "harness"
                elif REVIEW_BRIEF.match(text):
                    reason = "agent-launched"
                elif scheduled_at(norm, ts, scheduled):
                    reason = "automation"
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


def codex_prompts(since, launches: list[dict], scheduled: dict[str, datetime.datetime]) -> list[dict]:
    root = home() / ".codex"
    files = codex_rollouts()
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
                    # measured: every message a T3 launch sent into Codex lacks a client_id, and no message with one
                    # matched a launch, so an item with a client_id is never agent-launched
                    link = None if "client_id" in item else codex_launch(text, ts, cwd, session, launches)
                    if session_reason:
                        reason = session_reason
                    elif DELEGATION_NOTICE.match(text) or DELEGATED_RESULT.match(text) or T3_NOTICE.match(text):
                        reason = "notification"  # T3 Code writes these into the thread it runs, as in Claude
                    elif "client_id" not in item and HEARTBEAT.match(text):
                        reason = "automation"  # a Codex heartbeat automation posting into this thread
                    elif DELEGATED.match(text):
                        reason = "subagent"
                    elif text.startswith(HARNESS + (ARTIFACT_SUMMARY,)) or text == RESUME:
                        reason = "harness"
                    elif link == "linked" or ("client_id" not in item and REVIEW_BRIEF.match(text)):
                        reason = "agent-launched"  # an agent sent it through a T3 launch tool (see launch evidence in Claude)
                    elif scheduled_at(" ".join(text.split()), ts, scheduled):
                        reason = "automation"
                    elif any(prompt in text for prompt in automation_prompts):
                        reason = "automation"
                    elif link == "unlinked":
                        reason = UNKNOWN  # an agent sent this exact text into another directory or thread: nothing here settles it
                    else:
                        reason = None
                    prompts.append({"ts": ts.isoformat(timespec="seconds"), "tool": "codex", "project": cwd, "session": session,
                                    "text": scrub(text), "excluded": reason})
    return prompts


def collect(since, tool: str, pattern: str | None, include_automated: bool = False) -> tuple[list[dict], dict]:
    """Prompts to show, and coverage: how many were included and excluded, by reason."""
    found = []
    launches = agent_launches(since)  # from Claude transcripts: the T3 and `claude -p` calls agents made, for both tools
    scheduled = scheduled_prompts()
    if tool in ("all", "claude"):
        found += claude_prompts(since, launches, scheduled)
    if tool in ("all", "codex"):
        found += codex_prompts(since, launches, scheduled)
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
