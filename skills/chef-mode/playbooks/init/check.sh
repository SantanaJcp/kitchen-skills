#!/bin/sh
# bin/check: this project's one check contract. It is yours: edit it.
# Tiers: commit (the pre-commit hook), integrate (before a merge), nightly (on a schedule) and verify-tree
# (a whole tree, as `kitchen integrate` checks it). `bin/check --list` prints them.
# Every command here was run in this repo and seen to pass, and the commit tier was seen to fail on a planted defect.
# A tier with no command fails: an empty check is never green.
set -eu
# The heavy tiers queue for one of the kitchen's slots, so parallel agents do not all build at once; without the kitchen
# (CI) there is no queue. commit stays out: a pre-commit hook waits on it. A project whose commit tier builds may add it.
case "${1:-}" in
  integrate|nightly|verify-tree)
    if [ -z "${KITCHEN_SLOT:-}" ] && command -v kitchen >/dev/null 2>&1; then exec kitchen slot -- "$0" "$@"; fi ;;
esac
cd "$(dirname "$0")/.."

tier_commit() {
  # the fast checks: lint and the quick tests, seconds to a minute
  echo "bin/check commit: no command yet" >&2; exit 1
}

tier_integrate() {
  # everything a merge needs: the full build and every test
  echo "bin/check integrate: no command yet" >&2; exit 1
}

tier_nightly() {
  tier_integrate
}

tier_verify_tree() {
  tier_integrate
}

case "${1:-}" in
  --list) printf '%s\n' commit integrate nightly verify-tree ;;
  commit) tier_commit ;;
  integrate) tier_integrate ;;
  nightly) tier_nightly ;;
  verify-tree) tier_verify_tree ;;
  *) echo "usage: bin/check --list | commit | integrate | nightly | verify-tree" >&2; exit 2 ;;
esac
