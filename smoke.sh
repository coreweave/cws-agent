#!/usr/bin/env bash
# End-to-end smoke test for cws-agent against the live platform.
# Validates: launch+bootstrap (Claude Code install), snapshot, down,
# restore-from-snapshot (bootstrap restores the ephemeral binary), prune, cleanup.
# Never prints credentials.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${1:-cwsa-smoke-$(date +%s)-$$}"
if ! [[ "$NAME" =~ ^[a-z0-9][a-z0-9-]{0,39}$ ]]; then
  echo "smoke sandbox name must match [a-z0-9][a-z0-9-]{0,39}" >&2
  exit 1
fi

if [ -z "${CWSANDBOX_API_KEY:-}" ] && [ -z "${WANDB_API_KEY:-}" ]; then
  echo "set CWSANDBOX_API_KEY (or WANDB_API_KEY) before running the smoke test" >&2
  exit 1
fi

step() { echo; echo "=== [$(date +%H:%M:%S)] $* ==="; }

# Only clean up a sandbox this invocation successfully created. In particular,
# a failed launch against an existing name must never stop someone else's box.
SMOKE_OWNS_SANDBOX=0
cleanup() {
  local code=$?
  trap - EXIT INT TERM
  if [ "$SMOKE_OWNS_SANDBOX" -eq 1 ]; then
    "$DIR/cws-agent" down "$NAME" --no-snapshot >&2 || true
    "$DIR/cws-agent" prune "$NAME" --keep 0 >&2 || true
  fi
  exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# An explicit reused name may still own old snapshots even without a running
# sandbox. Refuse it rather than deleting those snapshots in our cleanup.
EXISTING_SNAPSHOTS="$("$DIR/cws-agent" snapshots "$NAME")"
if [ -n "$EXISTING_SNAPSHOTS" ]; then
  echo "smoke name '$NAME' already has snapshots; choose a fresh name" >&2
  exit 1
fi

MARKER="persisted-$(date +%s)"

step "launch --detach (creates sandbox, installs Claude Code into /opt/agent)"
"$DIR/cws-agent" launch --name "$NAME" --lifetime 30m --detach
SMOKE_OWNS_SANDBOX=1

step "seed a marker into /workspace/project (proves persistence across restore)"
"$DIR/cws-agent" exec "$NAME" "echo $MARKER > /workspace/project/marker.txt && cat /workspace/project/marker.txt"

step "list"
"$DIR/cws-agent" list

step "snapshot (while RUNNING)"
"$DIR/cws-agent" snapshot "$NAME"

step "down (snapshot + stop)"
"$DIR/cws-agent" down "$NAME"

step "restore (restore latest snapshot into a fresh sandbox)"
"$DIR/cws-agent" restore "$NAME" --lifetime 30m

step "verify marker survived the stop/restore round-trip"
GOT="$("$DIR/cws-agent" exec "$NAME" "cat /workspace/project/marker.txt" | tr -d '[:space:]')"
if [ "$GOT" = "$MARKER" ]; then
  echo "  OK: marker '$GOT' survived snapshot round-trip"
else
  echo "  MISMATCH: expected '$MARKER' got '$GOT'" >&2
  exit 1
fi

step "status after restore"
"$DIR/cws-agent" status "$NAME"

step "cleanup: down --no-snapshot + prune --keep 0"
"$DIR/cws-agent" down "$NAME" --no-snapshot
"$DIR/cws-agent" prune "$NAME" --keep 0
SMOKE_OWNS_SANDBOX=0

step "PASS"
