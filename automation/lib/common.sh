#!/usr/bin/env bash
# Shared helpers for the nightly guard. Usage: source common.sh <project>
# Config: ${KITCHEN_CONFIG:-~/.config/kitchen}/automation/<project>.env (private, never in this repo)
# State:  ${KITCHEN_STATE:-~/.local/state/kitchen}/automation/<project>/ and nightly/<project>.jsonl
set -euo pipefail
# The job only ever works on its own clone; a GIT_DIR inherited from a git hook would redirect it.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_COMMON_DIR GIT_PREFIX GIT_NAMESPACE

KITCHEN_AUTOMATION="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="${1:?usage: nightly-guard <project>}"
CONFIG_ROOT="${KITCHEN_CONFIG:-$HOME/.config/kitchen}"
STATE_ROOT="${KITCHEN_STATE:-$HOME/.local/state/kitchen}"
CONFIG="$CONFIG_ROOT/automation/$PROJECT.env"
[ -f "$CONFIG" ] || { echo "no config: $CONFIG" >&2; exit 2; }
# shellcheck source=/dev/null
source "$CONFIG"

STATE_DIR="$STATE_ROOT/automation/$PROJECT"
CLONE_DIR="$STATE_DIR/clone"
LOG_DIR="$STATE_DIR/logs"
NIGHTLY_RECORD="$STATE_ROOT/nightly/$PROJECT.jsonl"
mkdir -p "$LOG_DIR" "$(dirname "$NIGHTLY_RECORD")"

# launchd starts with a bare PATH.
export PATH="${EXTRA_PATH:-}:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# A step is name|command with both parts non-blank (`bash -c ""` exits 0 and proves nothing).
malformed_steps() { # entries...
  local entry name cmd
  for entry in "$@"; do
    if [[ "$entry" != *"|"* ]]; then printf '%s\n' "${entry:-<empty>}"; continue; fi
    name="${entry%%|*}"; cmd="${entry#*|}"
    if [ -z "${name//[[:space:]]/}" ] || [ -z "${cmd//[[:space:]]/}" ]; then printf '%s\n' "$entry"; fi
  done
}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

bounded() { python3 "$KITCHEN_AUTOMATION/lib/bounded.py" "$@"; }
NET_TIMEOUT="${NET_TIMEOUT_SECONDS:-600}"

gh_bounded() {
  local gh_bin="${KITCHEN_REAL_GH:-$(command -v gh || true)}"
  [ -n "$gh_bin" ] && [ -x "$gh_bin" ] || { log "gh is not installed"; return 127; }
  bounded "${GH_TIMEOUT_SECONDS:-120}" "$gh_bin" "$@"
}

WARNINGS=()
warn() { WARNINGS+=("$1"); log "WARNING: $1"; }

redact() { python3 "$KITCHEN_AUTOMATION/lib/redact.py"; }

notify() {
  case "${KITCHEN_NOTIFY:-}" in
    "") python3 "$KITCHEN_AUTOMATION/lib/notify.py" "$1" "$2" ;;
    off) log "INFO: notifications off by configuration (KITCHEN_NOTIFY=off), not shown: $1" ;;
    *) echo "kitchen notify: KITCHEN_NOTIFY is '${KITCHEN_NOTIFY:0:40}', and only off (or unset) is valid; not shown: $1" >&2
       return 1 ;;
  esac
}

open_issue_number() { # label title
  gh_bounded issue list -R "$GH_REPO" --label "$1" --state open --search "in:title \"$2\"" --json number --jq '.[0].number // empty'
}

report_issue() { # label color label-description title body-file
  local existing url
  ISSUE_NUMBER=""
  gh_bounded label create "$1" -R "$GH_REPO" --color "$2" --description "$3" >/dev/null 2>&1 || true
  existing="$(open_issue_number "$1" "$4")" || return 1
  if [ -n "$existing" ]; then
    gh_bounded issue comment "$existing" -R "$GH_REPO" --body-file "$5" >/dev/null || return 1
    ISSUE_NUMBER="$existing"
    log "commented on issue #$existing"
  else
    url="$(gh_bounded issue create -R "$GH_REPO" --title "$4" --label "$1" --body-file "$5")" || return 1
    [[ "${url##*/}" =~ ^[1-9][0-9]*$ ]] || { log "gh issue create printed no issue URL: ${url:0:200}"; return 1; }
    ISSUE_NUMBER="${url##*/}"
    log "opened issue #$ISSUE_NUMBER: $4"
  fi
}

sync_clone() {
  if [ ! -d "$CLONE_DIR/.git" ]; then
    bounded "$NET_TIMEOUT" git clone --quiet "$REPO_URL" "$CLONE_DIR"
  fi
  bounded "$NET_TIMEOUT" git -C "$CLONE_DIR" -c core.hooksPath=/dev/null fetch --quiet --prune origin
  git -C "$CLONE_DIR" -c core.hooksPath=/dev/null checkout --quiet --force --detach "origin/$BRANCH"
  git -C "$CLONE_DIR" -c core.hooksPath=/dev/null reset --quiet --hard "origin/$BRANCH"
  git -C "$CLONE_DIR" -c core.hooksPath=/dev/null clean --quiet -fdx -e node_modules
  install_push_guard
}

install_push_guard() {
  local git_dir hooks_dir
  git_dir="$(git -C "$CLONE_DIR" rev-parse --absolute-git-dir)"
  hooks_dir="$git_dir/kitchen-hooks"
  mkdir -p "$hooks_dir"
  cat > "$hooks_dir/pre-push" <<HOOK
#!/usr/bin/env bash
while read -r local_ref local_sha remote_ref remote_sha; do
  case "\$remote_ref" in
    refs/heads/dev|refs/heads/main|refs/heads/master|$(printf %q "refs/heads/$BRANCH"))
      echo "kitchen automation: refusing to push to \$remote_ref" >&2; exit 1 ;;
  esac
done
HOOK
  chmod +x "$hooks_dir/pre-push"
  git -C "$CLONE_DIR" config core.hooksPath "$hooks_dir"
}

start_docker() {
  export KITCHEN_DOCKER=unavailable
  if docker info >/dev/null 2>&1; then KITCHEN_DOCKER=ready; return 0; fi
  [ "$(uname)" = Darwin ] || return 0
  log "docker not running; starting Docker Desktop"
  if ! open -a Docker >/dev/null 2>&1; then log "open -a Docker failed"; return 0; fi
  for _ in $(seq 1 "${DOCKER_WAIT_TRIES:-60}"); do
    if docker info >/dev/null 2>&1; then KITCHEN_DOCKER=ready; return 0; fi
    sleep "${DOCKER_WAIT_SECONDS:-5}"
  done
}

docker_ready() {
  if [ "$DOCKER_BEFORE_JOB" != ready ]; then
    log "docker was not usable before the job started"
    return 1
  fi
  docker info >/dev/null 2>&1 && return 0
  log "docker is not available"
  return 1
}

record_nightly() { # status failed_step cleanup sha run_id log started steps [warning...]
  python3 "$KITCHEN_AUTOMATION/lib/record.py" "$NIGHTLY_RECORD" write "$@"
}

close_stale_records() { # current-run-id
  python3 "$KITCHEN_AUTOMATION/lib/record.py" "$NIGHTLY_RECORD" close-stale "$1"
}

if [ -z "${KITCHEN_LOCK_STATE:-}" ]; then
  [ "${NEEDS_DOCKER:-0}" != 1 ] || start_docker
  export KITCHEN_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
  exec python3 "$KITCHEN_AUTOMATION/lib/supervise.py" "$STATE_DIR/job.lock" "${LOCK_WAIT_SECONDS:-3600}" "${LOCK_POLL_SECONDS:-5}" \
    --watch "$CLONE_DIR" --record "$NIGHTLY_RECORD" --run-id "$KITCHEN_RUN_ID" \
    -- "$BASH" "$0" "$PROJECT"
fi
LOCK_STATE="$KITCHEN_LOCK_STATE"
RUN_ID="$KITCHEN_RUN_ID"
DOCKER_BEFORE_JOB="${KITCHEN_DOCKER:-}"
unset KITCHEN_LOCK_STATE KITCHEN_RUN_ID KITCHEN_DOCKER
