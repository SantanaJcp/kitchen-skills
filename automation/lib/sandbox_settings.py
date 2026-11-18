#!/usr/bin/env python3
"""Claude Code settings that put an OS boundary around the gardener's tools.

  sandbox_settings.py [--format claude|srt] --read PATH... [--write PATH...] [--domain NAME...] [--socket PATH...]

--format claude (default) writes Claude Code settings for the agent; --format srt writes the same boundary as a
sandbox-runtime (srt) settings file, for the job's own verification of the agent's tree (see weekly-gardener).

Reads are an allowlist (Claude Code settings reference: permissions.blockReadsOutsideWorkingDirectories,
sandbox.filesystem.denyRead / allowRead, "the rule with the narrower path applies"):
- blockReadsOutsideWorkingDirectories makes Read, Grep and Glob refuse paths outside the working
  directories, and denies sandboxed Bash the home directory and the other user roots (/Users, /home...);
- denyRead "~/" denies the whole home directory to Bash even where the block re-opens files, and
  allowRead re-opens exactly the --read paths (the clone, the run's work dir, the metrics history, and
  the tool or cache paths the project's build needs, from its config);
- the credential locations below stay denied even inside an allowed path (narrower deny wins).
Writes go only to the working directory, the session temp dir and the --write paths; network only to
the --domain names. failIfUnavailable and allowUnsandboxedCommands=false leave no way out. The attribution
setting (empty commit and pr text, no session link) keeps Claude Code's trailers out of the agent's commits. The claude
process itself stays outside the sandbox, so its own login and the model API keep working.
"""
import argparse
import json
import sys

CREDENTIAL_DIRS = ["~/.ssh", "~/Library/Keychains", "~/.config/gh", "~/.aws", "~/.config/gcloud", "~/.azure",
                   "~/.kube", "~/.docker", "~/.gnupg", "~/.codex", "~/.claude/projects"]
CREDENTIAL_FILES = ["~/.git-credentials", "~/.netrc", "~/.npmrc", "~/.pypirc", "~/.gitconfig"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--read", action="append", default=[], required=True)
    parser.add_argument("--write", action="append", default=[])
    parser.add_argument("--domain", action="append", default=[])
    parser.add_argument("--socket", action="append", default=[])
    parser.add_argument("--format", choices=["claude", "srt"], default="claude")
    args = parser.parse_args()
    reads = [path for path in args.read if path]
    writes = [path for path in args.write if path]
    domains = [d for d in args.domain if d]
    sockets = [s for s in args.socket if s]
    if args.format == "srt":
        # srt refuses a settings file that omits a field, so every list is written even when empty.
        print(json.dumps({
            "filesystem": {"denyRead": ["~/"] + CREDENTIAL_DIRS + CREDENTIAL_FILES, "allowRead": reads,
                           "allowWrite": writes, "denyWrite": CREDENTIAL_DIRS + CREDENTIAL_FILES},
            "network": {"allowedDomains": domains, "deniedDomains": [], "allowUnixSockets": sockets},
        }, indent=2))
        return 0
    deny_rules = []
    for path in CREDENTIAL_DIRS:
        deny_rules += [f"Read({path}/**)", f"Edit({path}/**)"]
    for path in CREDENTIAL_FILES:
        deny_rules += [f"Read({path})", f"Edit({path})"]
    settings = {
        "sandbox": {
            "enabled": True,
            "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {
                "denyRead": ["~/"] + CREDENTIAL_DIRS + CREDENTIAL_FILES,
                "allowRead": reads,
                "allowWrite": writes,
            },
            "network": {"allowedDomains": domains, "allowUnixSockets": sockets},
        },
        "permissions": {"blockReadsOutsideWorkingDirectories": True, "deny": deny_rules},
        # The job publishes the agent's commits: no Co-Authored-By or session trailer a project may forbid.
        "attribution": {"commit": "", "pr": "", "sessionUrl": False},
    }
    print(json.dumps(settings, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
