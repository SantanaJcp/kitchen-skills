"""Shared by the kitchen agent hooks: read a PreToolUse payload and list the commands a Bash call would run.

Claude Code and Codex send the same shape on stdin ({"tool_name": "Bash", "tool_input": {"command": ...},
"cwd": ...}) and both block a tool call when the hook exits 2, showing stderr to the agent. Codex may send
the command as an argv list; it is joined back into one shell string.

The parser is a guard, not a shell: it knows quoting, ;, &&, ||, pipes, subshells, $(...), backticks, heredocs,
redirections, `VAR=x` prefixes, wrappers (command, env, sudo, xargs, find -exec ...), `sh -c` and `eval`. What it
cannot see (aliases, scripts run from files, commands fed to a shell on stdin) is listed as a known limit in
tests/corpus/hooks/cases.json. It runs on the system python3, so it stays compatible with Python 3.9.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

MAX_DEPTH = 8
OPS = sorted([";;&", ";&", "&>>", "<<-", "<<<", "&&", "||", ";;", "|&", ">>", "<<", ">&", "<&", "&>", ">|", "<>",
              ";", "&", "|", "(", ")", "<", ">"], key=len, reverse=True)
REDIRECTS = {">>", "<<", "<<-", "<<<", ">&", "<&", "&>", "&>>", ">|", "<>", "<", ">"}
KEYWORDS = {"if", "then", "else", "elif", "do", "while", "until", "!", "{", "}", "fi", "done", "esac"}
SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "mksh", "fish"}
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\+?=")


class ParseError(Exception):
    pass


# ---------------------------------------------------------------- lexer

@dataclass
class Word:
    segs: list  # ("lit", text) quoted literal | ("raw", text) unquoted | ("var", name) | ("sub", tokens) | ("dyn", text)


@dataclass
class Op:
    text: str


ANSI_C_SIMPLE = {"a": "\a", "b": "\b", "e": "\x1b", "E": "\x1b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
                 "v": "\v", "\\": "\\", "'": "'", '"': '"', "?": "?"}


def ansi_c(s: str, j: int) -> tuple[str, int]:
    """Decode bash $'...' quoting from s[j:] (just past the opening quote); return (text, index after the quote)."""
    buf = []
    while j < len(s) and s[j] != "'":
        if s[j] != "\\" or j + 1 >= len(s):
            buf.append(s[j])
            j += 1
            continue
        c = s[j + 1]
        if c in ANSI_C_SIMPLE:
            buf.append(ANSI_C_SIMPLE[c])
            j += 2
        elif c in "01234567":
            digits = re.match(r"[0-7]{1,3}", s[j + 1:]).group(0)
            buf.append(chr(int(digits, 8) & 0xFF))
            j += 1 + len(digits)
        elif c in "xuU":
            width = {"x": 2, "u": 4, "U": 8}[c]
            digits = re.match(r"[0-9A-Fa-f]{0,%d}" % width, s[j + 2:]).group(0)
            if not digits:
                buf.append("\\" + c)
            else:
                code = int(digits, 16)
                if code > 0x10FFFF:
                    raise ParseError("an ANSI-C escape names no character")
                buf.append(chr(code))
            j += 2 + len(digits)
        elif c == "c" and j + 2 < len(s):
            buf.append(chr(ord(s[j + 2].upper()) ^ 0x40))
            j += 3
        else:
            buf.append("\\" + c)
            j += 2
    if j >= len(s):
        raise ParseError("unterminated $'")
    return "".join(buf), j + 1


class Lexer:
    def __init__(self, text: str):
        self.s = text
        self.heredocs: list[tuple[str, bool, bool]] = []  # (delimiter, strip tabs, expands)

    def lex(self, i: int = 0, stop_paren: bool = False) -> tuple[list, int]:
        s, tokens, depth = self.s, [], 0
        segs: list = []
        started = False
        want_delim: str | None = None

        def flush():
            nonlocal segs, started, want_delim
            if started:
                word = Word(segs)
                if want_delim is not None:
                    quoted = any(kind == "lit" for kind, _ in segs)
                    delim = "".join(text for kind, text in segs if kind in ("lit", "raw"))
                    self.heredocs.append((delim, want_delim == "<<-", not quoted))
                    want_delim = None
                tokens.append(word)
            segs, started = [], False

        while i < len(s):
            c = s[i]
            if c in " \t\r":
                flush()
                i += 1
            elif c == "\n":
                flush()
                tokens.append(Op("\n"))
                i = self.skip_heredocs(i + 1, tokens)
            elif c == "#" and not started:
                while i < len(s) and s[i] != "\n":
                    i += 1
            elif c == "\\":
                if i + 1 < len(s) and s[i + 1] == "\n":
                    i += 2
                else:
                    segs.append(("lit", s[i + 1:i + 2]))
                    started = True
                    i += 2
            elif c == "'":
                end = s.find("'", i + 1)
                if end == -1:
                    raise ParseError("unterminated single quote")
                segs.append(("lit", s[i + 1:end]))
                started = True
                i = end + 1
            elif c == '"':
                i = self.double_quoted(i + 1, segs)
                started = True
            elif c == "$":
                i = self.dollar(i, segs, quoted=False)
                started = True
            elif c == "`":
                i = self.backtick(i, segs)
                started = True
            elif c in "<>" and i + 1 < len(s) and s[i + 1] == "(":
                inner, end = Lexer(s).lex_sub(i + 2)
                segs.append(("sub", inner))
                started = True
                i = end
            elif c in ";&|()<>":
                op = next(o for o in OPS if s.startswith(o, i))
                if stop_paren and op == ")" and depth == 0:
                    flush()
                    return tokens, i + 1
                if op in REDIRECTS and started and all(k == "raw" and t.isdigit() for k, t in segs):
                    segs, started = [], False  # `2>` names a file descriptor, not an argument
                flush()
                depth += op == "("
                depth -= op == ")" and depth > 0
                tokens.append(Op(op))
                if op in ("<<", "<<-"):
                    want_delim = op
                i += len(op)
            else:
                start = i
                while i < len(s) and s[i] not in " \t\r\n\\'\"$`;&|()<>":
                    i += 1
                segs.append(("raw", s[start:i]))
                started = True
        if stop_paren:
            raise ParseError("unterminated $( or (")
        flush()
        return tokens, i

    def lex_sub(self, i: int) -> tuple[list, int]:
        return self.lex(i, stop_paren=True)

    def skip_heredocs(self, i: int, tokens: list) -> int:
        s = self.s
        while self.heredocs:
            delim, strip, expands = self.heredocs.pop(0)
            body_start = i
            while True:
                if i >= len(s):
                    return i  # an unterminated heredoc runs to the end, as in bash
                end = s.find("\n", i)
                end = len(s) if end == -1 else end
                line = s[i:end]
                if (line.lstrip("\t") if strip else line) == delim:
                    if expands:
                        self.scan_body(s[body_start:i], tokens)
                    i = end + 1
                    break
                i = end + 1
        return i

    def scan_body(self, body: str, tokens: list) -> None:
        """An unquoted heredoc delimiter means $(...) and backticks in the body run."""
        sub = Lexer(body)
        segs: list = []
        j = 0
        while j < len(body):
            if body[j] == "\\":
                j += 2
            elif body[j] == "$":
                j = sub.dollar(j, segs, quoted=True)
            elif body[j] == "`":
                j = sub.backtick(j, segs)
            else:
                j += 1
        subs = [seg for seg in segs if seg[0] == "sub"]
        if subs:
            tokens.append(Op(";"))
            tokens.append(Word(subs))
            tokens.append(Op(";"))

    def double_quoted(self, i: int, segs: list) -> int:
        s, buf = self.s, []
        while i < len(s):
            c = s[i]
            if c == '"':
                segs.append(("lit", "".join(buf)))
                return i + 1
            if c == "\\" and i + 1 < len(s) and s[i + 1] in '"\\$`\n':
                if s[i + 1] != "\n":
                    buf.append(s[i + 1])
                i += 2
            elif c in "$`":
                segs.append(("lit", "".join(buf)))
                buf = []
                i = self.dollar(i, segs, quoted=True) if c == "$" else self.backtick(i, segs)
            else:
                buf.append(c)
                i += 1
        raise ParseError("unterminated double quote")

    def dollar(self, i: int, segs: list, quoted: bool) -> int:
        s = self.s
        nxt = s[i + 1:i + 2]
        if nxt == "(":
            inner, end = Lexer(s).lex_sub(i + 2)
            segs.append(("sub", inner))
            return end
        if nxt == "{":
            return self.braced(i, segs, quoted)
        if nxt == "'" and not quoted:
            text, end = ansi_c(s, i + 2)
            segs.append(("lit", text))
            return end
        if nxt == '"' and not quoted:
            return self.double_quoted(i + 2, segs)
        match = NAME_RE.match(s, i + 1)
        if match:
            segs.append(("var", match.group(0)))
            return match.end()
        if nxt and nxt in "@*#?$!-0123456789":
            segs.append(("dyn", s[i:i + 2]))
            return i + 2
        segs.append(("lit" if quoted else "raw", "$"))
        return i + 1

    def braced(self, i: int, segs: list, quoted: bool) -> int:
        """${...}: a plain ${NAME} is a variable; anything else is dynamic, and the command substitutions
        inside it (${X:-$(cmd)}, nested ${...}, backticks) still run, so they are collected too."""
        s, j, inner = self.s, i + 2, []
        while j < len(s) and s[j] != "}":
            c = s[j]
            if c == "\\":
                j += 2
            elif c == "'" and not quoted:
                end = s.find("'", j + 1)
                if end == -1:
                    raise ParseError("unterminated single quote")
                j = end + 1
            elif c == '"':
                j = self.double_quoted(j + 1, inner)
            elif c == "$":
                j = self.dollar(j, inner, quoted=True)
            elif c == "`":
                j = self.backtick(j, inner)
            else:
                j += 1
        if j >= len(s):
            raise ParseError("unterminated ${")
        body = s[i + 2:j]
        if NAME_RE.fullmatch(body):
            segs.append(("var", body))
        else:
            segs.append(("dyn", s[i:j + 1]))
            segs.extend(seg for seg in inner if seg[0] == "sub")
        return j + 1

    def backtick(self, i: int, segs: list) -> int:
        s, j, buf = self.s, i + 1, []
        while j < len(s) and s[j] != "`":
            if s[j] == "\\" and j + 1 < len(s):
                buf.append(s[j + 1])
                j += 2
            else:
                buf.append(s[j])
                j += 1
        if j >= len(s):
            raise ParseError("unterminated backtick")
        inner, _ = Lexer("".join(buf)).lex()
        segs.append(("sub", inner))
        return j + 1


# ---------------------------------------------------------------- expansion and commands

TEMP = object()  # a value known to be a fresh path inside the temp dir: $(mktemp ...)
# Keys in a variables dict that are not variable names: whether the inherited environment was cleared
# (env -i, sudo), and which shell variables were exported to child processes.
CLEAN, EXPORTED = "\0clean", "\0exported"


@dataclass
class Arg:
    value: str
    dynamic: bool = False  # depends on something the hook cannot know (command output, unset variable)
    glob: bool = False     # has unquoted * ? [


@dataclass
class Cmd:
    argv: list            # list[Arg]
    cwd: str | None       # None when a `cd` made it unknowable
    env: dict = field(default_factory=dict)  # VAR=value prefixes: name -> Arg
    more_args: bool = False  # xargs / find -exec append operands the hook cannot see
    clean: bool = False      # runs without the inherited environment (env -i, sudo)

    @property
    def name(self) -> str | None:
        if not self.argv or self.argv[0].dynamic:
            return None
        return os.path.basename(self.argv[0].value)


def mktemp_in_temp(tokens: list) -> bool:
    words = [t for t in tokens if isinstance(t, Word)]
    if len(words) != len(tokens) or not words:
        return False
    values = [expand(w, {})[0] for w in words]
    if os.path.basename(values[0].value) != "mktemp" or any(v.dynamic for v in values):
        return False
    for arg in values[1:]:
        if arg.value.startswith("-"):
            if any(ch not in "dqtu" for ch in arg.value[1:]):
                return False
        elif "/" in arg.value:
            return False
    return True


def lookup(name: str, variables: dict):
    """A variable's value: a string, TEMP, or None when the hook cannot know it."""
    if name in variables:
        return variables[name]
    if variables.get(CLEAN):
        return None
    return os.environ.get(name)


def temp_path() -> str:
    return os.path.join(tempfile.gettempdir(), "kitchen-mktemp")


def env_arg(name: str, value) -> Arg:
    if value is TEMP:
        return Arg(temp_path())
    return Arg(value) if isinstance(value, str) else Arg("$" + name, dynamic=True)


def exported_env(variables: dict) -> dict:
    """The shell variables a child process inherits, as name -> Arg."""
    exported = variables.get(EXPORTED, frozenset())
    return {name: env_arg(name, value) for name, value in variables.items()
            if not name.startswith("\0") and (name in exported or (name in os.environ and not variables.get(CLEAN)))}


def child_variables(cmd: "Cmd") -> dict:
    """What a child shell (sh -c) starts with: the command's environment and nothing else of this shell."""
    child = {name: (None if arg.dynamic else arg.value) for name, arg in cmd.env.items()}
    child[EXPORTED] = frozenset(cmd.env)
    if cmd.clean:
        child[CLEAN] = True
    return child


def expand(word: Word, variables: dict) -> tuple[Arg, list]:
    """The word's value under the known variables, and the token lists of its command substitutions."""
    parts, dynamic, glob, subs = [], False, False, []
    for index, (kind, text) in enumerate(word.segs):
        if kind == "lit":
            parts.append(text)
        elif kind == "raw":
            if index == 0 and text.startswith("~"):
                head, sep, rest = text.partition("/")
                home = lookup("HOME", variables)
                if head == "~" and isinstance(home, str):
                    text = home + sep + rest
                else:
                    dynamic = True
            glob = glob or any(ch in text for ch in "*?[")
            parts.append(text)
        elif kind == "var":
            value = lookup(text, variables)
            if value is TEMP:
                parts.append(temp_path())
            elif isinstance(value, str):
                parts.append(value)
            else:
                dynamic = True
                parts.append("$" + text)
        elif kind == "sub":
            subs.append(text)
            if index == 0 and mktemp_in_temp(text):
                parts.append(os.path.join(tempfile.gettempdir(), "kitchen-mktemp"))
            else:
                dynamic = True
                parts.append("$(...)")
        else:
            dynamic = True
            parts.append(text)
    return Arg("".join(parts), dynamic, glob), subs


def join_path(cwd: str | None, path: str) -> str | None:
    if os.path.isabs(path):
        return path
    return os.path.join(cwd, path) if cwd else None


class Extractor:
    def __init__(self):
        self.out: list[Cmd] = []

    def script(self, text: str, cwd: str | None, variables: dict, depth: int) -> None:
        tokens, _ = Lexer(text).lex()
        self.tokens(tokens, cwd, variables, depth)

    def tokens(self, tokens: list, cwd: str | None, variables: dict, depth: int) -> None:
        if depth > MAX_DEPTH:
            raise ParseError("commands nested too deeply to check")
        stack: list[tuple[str | None, dict]] = []
        variables = dict(variables)
        words: list[Word] = []
        skip_next = False
        for token in tokens + [Op(";")]:
            if isinstance(token, Word):
                if skip_next:
                    skip_next = False
                    for kind, text in token.segs:
                        if kind == "sub":
                            self.tokens(text, cwd, variables, depth + 1)
                    continue
                words.append(token)
                continue
            skip_next = token.text in REDIRECTS
            if token.text in REDIRECTS:
                continue
            if words:
                cwd = self.simple(words, cwd, variables, depth)
                words = []
            if token.text == "(":
                stack.append((cwd, dict(variables)))
            elif token.text == ")" and stack:
                cwd, variables = stack.pop()

    def simple(self, words: list, cwd: str | None, variables: dict, depth: int) -> str | None:
        """Record one simple command; return the cwd for the commands after it."""
        args, env = [], {}
        for word in words:
            arg, subs = expand(word, variables)
            for sub in subs:
                self.tokens(sub, cwd, variables, depth + 1)
            raw_first = word.segs[0] if word.segs else ("lit", "")
            match = ASSIGN_RE.match(raw_first[1]) if raw_first[0] == "raw" else None
            if not args and match:
                name = match.group(1)
                value_word = Word([("raw", raw_first[1][match.end():])] + list(word.segs[1:]))
                value, value_subs = expand(value_word, variables)
                temp = len(value_word.segs) == 2 and value_word.segs[0] == ("raw", "") and \
                    value_word.segs[1][0] == "sub" and mktemp_in_temp(value_word.segs[1][1])
                env[name] = TEMP if temp else value
                continue
            if not args and arg.value in KEYWORDS and not arg.dynamic:
                continue
            args.append(arg)
        if not args:
            for name, value in env.items():  # NAME=value alone sets a shell variable
                variables[name] = value if value is TEMP else (None if value.dynamic else value.value)
            return cwd
        prefix = {k: (Arg(temp_path()) if v is TEMP else v) for k, v in env.items()}
        self.resolve(Cmd(args, cwd, {**exported_env(variables), **prefix}, clean=bool(variables.get(CLEAN))), variables, depth)
        name = args[0].value if not args[0].dynamic else None
        if name in ("export", "local", "declare", "typeset", "readonly"):
            exports = name == "export" or any(a.value.startswith("-") and "x" in a.value for a in args[1:])
            for arg in args[1:]:
                if arg.value.startswith("-"):
                    continue
                m = ASSIGN_RE.match(arg.value)
                key = m.group(1) if m else arg.value
                if m:
                    variables[key] = None if arg.dynamic else arg.value[m.end():]
                if exports and NAME_RE.fullmatch(key):
                    variables[EXPORTED] = variables.get(EXPORTED, frozenset()) | {key}
        if name in ("cd", "pushd"):
            rest = [a for a in args[1:] if not a.value.startswith("-") or a.value == "-"]
            if not rest:
                home = lookup("HOME", variables)
                return home if isinstance(home, str) else None
            target = rest[0]
            if target.dynamic or target.value == "-":
                return None
            return join_path(cwd, target.value)
        if name == "popd":
            return None
        return cwd

    def resolve(self, cmd: Cmd, variables: dict, depth: int) -> None:
        """Peel wrappers (command, env, sudo, xargs, sh -c ...) and record the command that actually runs."""
        argv, name = cmd.argv, cmd.name
        self.out.append(cmd)
        if name is None or len(argv) < 2:
            return
        rest = argv[1:]

        def again(new_argv, **changes):
            if new_argv:
                clean = changes.get("clean", False)
                base = {} if clean else cmd.env
                self.resolve(Cmd(new_argv, changes.get("cwd", cmd.cwd), {**base, **changes.get("env", {})},
                                 changes.get("more_args", cmd.more_args), cmd.clean or clean), variables, depth + 1)

        def skip_options(args, with_value=(), stop_on=()):
            i = 0
            while i < len(args) and not args[i].dynamic and args[i].value.startswith("-") and args[i].value != "-":
                value = args[i].value
                if value == "--":
                    return i + 1
                if value in stop_on:
                    return None
                if value in with_value:
                    i += 1
                i += 1
            return i

        if name in ("command", "builtin"):
            i = 0
            while i < len(rest) and rest[i].value.startswith("-") and not rest[i].dynamic:
                if "v" in rest[i].value or "V" in rest[i].value:
                    return  # `command -v rm` only looks the name up
                i += 1
            again(rest[i:])
        elif name in ("nohup", "time", "exec", "caffeinate"):
            i = skip_options(rest, with_value=("-a",))
            again(rest[i:])
        elif name == "nice":
            again(rest[skip_options(rest, with_value=("-n",)):])
        elif name in ("sudo", "doas"):
            # sudo resets the environment by default: what the command sees is not this shell's
            i = skip_options(rest, with_value=("-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-U", "-T"))
            again(rest[i:], clean=True)
        elif name == "stdbuf":
            again(rest[skip_options(rest, with_value=("-i", "-o", "-e")):])
        elif name == "timeout":
            i = skip_options(rest, with_value=("-s", "-k"))
            again(rest[i + 1:])
        elif name == "env":
            i, env, cwd, clean = 0, {}, cmd.cwd, False
            while i < len(rest):
                arg = rest[i]
                if arg.dynamic:
                    break
                if arg.value in ("-i", "--ignore-environment", "-"):
                    clean, env = True, {}
                    i += 1
                    continue
                if arg.value in ("-u", "--unset"):
                    if i + 1 < len(rest):
                        env[rest[i + 1].value] = Arg("$" + rest[i + 1].value, dynamic=True)
                    i += 2
                    continue
                if arg.value in ("-C", "--chdir"):
                    nxt = rest[i + 1] if i + 1 < len(rest) else None
                    cwd = None if nxt is None or nxt.dynamic else join_path(cwd, nxt.value)
                    i += 2
                    continue
                if arg.value.startswith("--chdir="):
                    cwd = join_path(cwd, arg.value.split("=", 1)[1])
                elif arg.value in ("-S", "--split-string") or arg.value.startswith("--split-string="):
                    text = arg.value.split("=", 1)[1] if "=" in arg.value else (rest[i + 1].value if i + 1 < len(rest) else "")
                    tail = rest[i + 1:] if "=" in arg.value else rest[i + 2:]
                    # The arguments after the split string stay one argument each, as env passes them.
                    quoted = [shlex.quote(a.value) if not a.dynamic else '"$__kitchen_unknown"' for a in tail]
                    sub = Extractor()
                    sub.script(" ".join([text] + quoted), cwd, {**variables, "__kitchen_unknown": None}, depth + 1)
                    self.out.extend(sub.out)
                    return
                elif arg.value.startswith("-") and "i" in arg.value[1:] and not arg.value.startswith("--"):
                    clean, env = True, {}
                elif arg.value.startswith("-"):
                    pass
                elif ASSIGN_RE.match(arg.value):
                    key = ASSIGN_RE.match(arg.value).group(1)
                    env[key] = Arg(arg.value.split("=", 1)[1])
                else:
                    break
                i += 1
            again(rest[i:], env=env, cwd=cwd, clean=clean)
        elif name == "xargs":
            i = skip_options(rest, with_value=("-I", "-n", "-P", "-L", "-s", "-d", "-E", "-a"))
            again(rest[i:] or [Arg("echo")], more_args=True)
        elif name == "find":
            i = 0
            while i < len(rest):
                if rest[i].value in ("-exec", "-execdir", "-ok", "-okdir"):
                    j = i + 1
                    while j < len(rest) and rest[j].value not in (";", "+"):
                        j += 1
                    inner = [a for a in rest[i + 1:j] if a.value != "{}"]
                    again(inner, more_args=True)
                    i = j
                i += 1
        elif name in SHELLS:
            i, has_c = 0, False
            while i < len(rest) and not rest[i].dynamic and rest[i].value[:1] in "-+" and rest[i].value not in ("-", "--"):
                value = rest[i].value
                if value in ("-o", "+o", "-O", "+O"):
                    i += 1
                elif not value.startswith("--") and "c" in value[1:]:
                    has_c = True
                i += 1
            if has_c and i < len(rest):
                script = rest[i]
                if script.dynamic:
                    self.out.append(Cmd([Arg(script.value, dynamic=True)], cmd.cwd, cmd.env))
                    return
                sub = Extractor()
                sub.script(script.value, cmd.cwd, child_variables(cmd), depth + 1)
                self.out.extend(sub.out)
        elif name == "eval":
            if any(a.dynamic for a in rest):
                self.out.append(Cmd([Arg("eval", dynamic=True)], cmd.cwd, cmd.env))
                return
            sub = Extractor()
            sub.script(" ".join(a.value for a in rest), cmd.cwd, variables, depth + 1)
            self.out.extend(sub.out)


def commands(text: str, cwd: str | None) -> list[Cmd]:
    extractor = Extractor()
    extractor.script(text, cwd, {}, 0)
    return extractor.out


# ---------------------------------------------------------------- git

GIT_GLOBAL_WITH_VALUE = {"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix", "--config-env"}
# Variables that point git at other config files: what they contain is not known when the hook runs.
GIT_CONFIG_FILE_VARS = ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG")


@dataclass
class GitCall:
    sub: str | None        # None when the subcommand cannot be known
    args: list             # list[Arg] after the subcommand
    config: list           # [(key lowercased, value)] from -c and GIT_CONFIG_*; key "<unknown>" when it is dynamic
    global_opts: list      # [str] -C/--git-dir/--work-tree options to replay when asking git about the repo
    cmd: Cmd


def git_call(cmd: Cmd) -> GitCall | None:
    if cmd.name != "git":
        return None
    argv, i, config, global_opts = cmd.argv, 1, [], []
    while i < len(argv):
        arg = argv[i]
        if arg.dynamic:
            return GitCall(None, argv[i + 1:], config, global_opts, cmd)
        value = arg.value
        if not value.startswith("-"):
            break
        name, eq, attached = value.partition("=")
        if name in GIT_GLOBAL_WITH_VALUE and value != name or (name in GIT_GLOBAL_WITH_VALUE and eq):
            option_value = attached
        elif value in GIT_GLOBAL_WITH_VALUE:
            i += 1
            option_value = argv[i].value if i < len(argv) else ""
        elif value.startswith("-c") and len(value) > 2:
            name, option_value = "-c", value[2:]
        else:
            i += 1
            continue
        if name == "-c":
            key, _, val = option_value.partition("=")
            config.append((key.lower(), val))
        elif name == "--config-env":
            config.append((option_value.partition("=")[0].lower(), "<env>"))
        elif name in ("-C", "--git-dir", "--work-tree"):
            global_opts += [name, option_value]
        i += 1
    for key, val in cmd.env.items():
        if key in ("GIT_CONFIG_PARAMETERS",) or re.fullmatch(r"GIT_CONFIG_KEY_\d+", key):
            if val.dynamic:
                config.append(("<unknown>", "<env>"))
            elif key == "GIT_CONFIG_PARAMETERS":
                for found in re.findall(r"'([^']*)'", val.value) or [val.value]:
                    config.append((found.partition("=")[0].lower(), found.partition("=")[2]))
            else:
                config.append((val.value.lower(), "<env>"))
    if i >= len(argv):
        return GitCall("", [], config, global_opts, cmd)
    return GitCall(argv[i].value, argv[i + 1:], config, global_opts, cmd)


def config_files(call: GitCall) -> list[str]:
    """GIT_CONFIG_GLOBAL and friends set for this command: git will read config the hook has not seen."""
    return [name for name in GIT_CONFIG_FILE_VARS if name in call.cmd.env]


def run_git(call: GitCall, *args: str) -> str | None:
    """Ask git about the repo the command runs in. Its -c options are not replayed (a -c can run programs);
    guards block a command whose -c could change the answer instead."""
    if not call.cmd.cwd:
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({k: v.value for k, v in call.cmd.env.items() if k.startswith("GIT_") and not v.dynamic})
    try:
        result = subprocess.run(["git", "-C", call.cmd.cwd, *call.global_opts, *args], capture_output=True,
                                text=True, env=env, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def git_config_regexp(call: GitCall, pattern: str, *flags: str) -> "list[tuple[str, str]] | None":
    """(key, value) pairs of `git config --get-regexp`; [] when nothing matches; None when git could not answer
    (a read error or timeout is not "not configured")."""
    if not call.cmd.cwd:
        return None
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({k: v.value for k, v in call.cmd.env.items() if k.startswith("GIT_") and not v.dynamic})
    try:
        result = subprocess.run(["git", "-C", call.cmd.cwd, *call.global_opts, "config", *flags, "--get-regexp", pattern],
                                capture_output=True, text=True, env=env, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 1 and not result.stdout:
        return []
    if result.returncode != 0:
        return None
    return [tuple(line.split(" ", 1)) if " " in line else (line, "") for line in result.stdout.splitlines() if line]


# ---------------------------------------------------------------- hook entry point

def block(guard: str, message: str) -> None:
    sys.stderr.write(f"Blocked by the kitchen hook {guard}: {message}\n")
    sys.exit(2)


def payload_command(raw: str) -> tuple[str | None, str | None]:
    """(command, cwd) from the hook input; command None when this is not a shell call."""
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("the hook input is not a JSON object")
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("tool_name is missing")
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        command = " ".join(shlex.quote(part) for part in command)
    if not isinstance(command, str):
        if tool_name == "Bash":
            raise ValueError("tool_input.command is missing or not a string")
        return None, None
    cwd = payload.get("cwd")
    return command, cwd if isinstance(cwd, str) and cwd else None


def main(guard: str, check) -> None:
    """Run one guard over the payload on stdin: exit 0 to allow, exit 2 with the reason to block.

    A guard that cannot read its input or parse the command blocks: Claude Code and Codex treat only exit 2
    as a block, so any other failure (a traceback exits 1) would let the command run."""
    try:
        command, cwd = payload_command(sys.stdin.read())
    except Exception as error:  # noqa: BLE001 - includes RecursionError from deeply nested JSON
        block(guard, f"cannot read the hook input ({type(error).__name__}: {str(error)[:200]}). "
                     "The hook format may have changed; run `kitchen doctor`.")
        return
    if command is None:
        sys.exit(0)
    try:
        reasons = check(commands(command, cwd))
    except ParseError as error:
        block(guard, f"cannot parse this command to check it ({error}). Fix the quoting, or split it into simpler commands.")
        return
    except Exception as error:  # noqa: BLE001 - a crashed guard must not pass silently
        block(guard, f"the guard crashed ({type(error).__name__}: {error}). Report it; `kitchen check` runs the hook corpus.")
        return
    if reasons:
        block(guard, " ".join(dict.fromkeys(reasons)))
    sys.exit(0)
