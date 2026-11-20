# Automation

One scheduled job: `bin/nightly-guard <project>` runs a project's nightly check on a dedicated clone of its shared branch, opens or comments on one GitHub issue while red, closes it when green again, writes one JSON line to `~/.local/state/kitchen/nightly/<project>.jsonl`, and sends a local notification on red or incomplete runs.

Schedule it with `bin/install-schedule <project> [hour]` (default `02:00` daily): launchd on macOS, systemd user timer on Linux (requires `loginctl enable-linger $USER`).

## Project config

One file per project at `~/.config/kitchen/automation/<project>.env`, outside this public repo:

```bash
REPO_URL="git@github.com:owner/repo.git"
GH_REPO="owner/repo"
BRANCH="dev"
EXTRA_PATH="$HOME/.dotnet"            # optional; tools launchd cannot find on its own
NEEDS_DOCKER=1                        # optional; 1 fails the run if Docker is unavailable
GUARD_LABEL="nightly-guard"           # optional; GitHub issue label (default: nightly-guard)
GUARD_STEPS=(                         # required; name|command, run in bash -e -o pipefail; first failure stops
  "build|make build"
  "tests|make test"
)
CLEANUP_CMD="make stop"               # optional; runs after steps finish
STEP_TIMEOUT_SECONDS=3600             # optional; deadline per step (default: 3600)
LOCK_WAIT_SECONDS=3600                # optional; wait for an existing run before skipping as busy
GH_TIMEOUT_SECONDS=120                # optional; deadline for gh calls
NET_TIMEOUT_SECONDS=600               # optional; deadline for git clone/fetch
KITCHEN_NOTIFY="off"                  # optional; set to off on a headless timer user with no notification bus
```

## Nightly record

One JSON line per run in `~/.local/state/kitchen/nightly/<project>.jsonl`: `ts`, `started`, `sha`, `status` (`running`, `green`, `red`, `incomplete`), `failed_step`, `cleanup`, `steps` (`[{"name", "seconds", "status"}]`), `warnings`, `run_id`, `log`.

- A run without steps or with a malformed step is `incomplete` (`failed_step: config`), never green.
- Trial runs (`GUARD_ONLY="build"`) are neither recorded nor reported.
- `lib/supervise.py` holds a project lock, runs the job in its own process group, and stops leftover processes holding files in the clone (sparing processes older than the job).
- Log excerpts posted to GitHub issues and notifications are redacted with the same credential and denylist patterns as `kitchen retro`.
