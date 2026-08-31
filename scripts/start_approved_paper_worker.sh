#!/usr/bin/env bash
set -euo pipefail

CONTROL_LOCK_FILE="/tmp/marketai-approved-paper-control.lock"
LOCK_TIMEOUT_SECONDS="${MARKETAI_APPROVED_PAPER_CONTROL_LOCK_TIMEOUT_SECONDS:-300}"
SERVICE="marketai-approved-paper.service"

exec 201>"$CONTROL_LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT_SECONDS" 201; then
  echo "Could not acquire approved PAPER worker control lock within ${LOCK_TIMEOUT_SECONDS}s." >&2
  exit 1
fi

# This is the canonical operator start path. The readiness workflow takes the
# same lock around every systemd unit mutation, so a start and a readiness
# install/disable operation cannot pass each other between is-active checks.
if /usr/bin/systemctl is-active --quiet "$SERVICE"; then
  echo "$SERVICE is already active."
  exit 0
fi

sudo /usr/bin/systemctl start "$SERVICE"
if ! /usr/bin/systemctl is-active --quiet "$SERVICE"; then
  echo "$SERVICE did not become active." >&2
  sudo /usr/bin/systemctl status "$SERVICE" --no-pager -l || true
  exit 1
fi

echo "$SERVICE is active."
