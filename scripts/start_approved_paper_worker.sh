#!/usr/bin/env bash
set -euo pipefail

DEPLOY_LOCK_FILE="/tmp/marketai-deploy-ota.lock"
CONTROL_LOCK_FILE="/tmp/marketai-approved-paper-control.lock"
LOCK_TIMEOUT_SECONDS="${MARKETAI_APPROVED_PAPER_CONTROL_LOCK_TIMEOUT_SECONDS:-300}"
RUNTIME_DIR="/home/marko/marketai-repo"
STATE_DIR="/home/marko/marketai-deploy-state"
DEPLOYED_STATE_FILE="$STATE_DIR/last-deployed-backend.sha"
PREPARED_STATE_FILE="$STATE_DIR/approved-paper-prepared.sha"
PREPARED_ENV_FILE="$STATE_DIR/approved-paper-prepared.env"
PREPARED_ENV_DIGEST_FILE="$STATE_DIR/approved-paper-prepared.env.sha256"
SERVICE="marketai-approved-paper.service"

# Use the same lock order as readiness/deploy: deployment lock first,
# worker-control lock second. This keeps runtime movement, readiness mutation and
# explicit activation serialized with one ordering.
exec 200>"$DEPLOY_LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT_SECONDS" 200; then
  echo "Could not acquire MarketAI deploy/OTA lock within ${LOCK_TIMEOUT_SECONDS}s." >&2
  exit 1
fi

exec 201>"$CONTROL_LOCK_FILE"
if ! flock -w "$LOCK_TIMEOUT_SECONDS" 201; then
  echo "Could not acquire approved PAPER worker control lock within ${LOCK_TIMEOUT_SECONDS}s." >&2
  exit 1
fi

for state_file in \
  "$DEPLOYED_STATE_FILE" \
  "$PREPARED_STATE_FILE" \
  "$PREPARED_ENV_FILE" \
  "$PREPARED_ENV_DIGEST_FILE"; do
  if [ ! -f "$state_file" ]; then
    echo "Required PAPER runtime state file is missing: $state_file" >&2
    exit 1
  fi
done

DEPLOYED_SHA="$(tr -d '[:space:]' < "$DEPLOYED_STATE_FILE")"
PREPARED_SHA="$(tr -d '[:space:]' < "$PREPARED_STATE_FILE")"
if [ -z "$DEPLOYED_SHA" ] || [ -z "$PREPARED_SHA" ]; then
  echo "Deployed/prepared PAPER runtime SHA must not be empty." >&2
  exit 1
fi

cd "$RUNTIME_DIR"
RUNTIME_SHA="$(git rev-parse HEAD)"
if [ "$RUNTIME_SHA" != "$PREPARED_SHA" ] || [ "$DEPLOYED_SHA" != "$PREPARED_SHA" ]; then
  echo "Refusing PAPER start: prepared=${PREPARED_SHA}, deployed=${DEPLOYED_SHA}, runtime=${RUNTIME_SHA}. Run readiness again for the currently deployed SHA." >&2
  exit 1
fi

if [ "$(git branch --show-current)" != "feature/trading-system-foundation" ]; then
  echo "Refusing PAPER start: production runtime is not on feature/trading-system-foundation." >&2
  exit 1
fi
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "Refusing PAPER start: production runtime has tracked working-tree changes." >&2
  exit 1
fi

EXPECTED_ENV_DIGEST="$(tr -d '[:space:]' < "$PREPARED_ENV_DIGEST_FILE")"
if [ -z "$EXPECTED_ENV_DIGEST" ]; then
  echo "Refusing PAPER start: prepared environment digest is empty." >&2
  exit 1
fi
ACTUAL_ENV_DIGEST="$(sha256sum "$PREPARED_ENV_FILE" | awk '{print $1}')"
if [ "$ACTUAL_ENV_DIGEST" != "$EXPECTED_ENV_DIGEST" ]; then
  echo "Refusing PAPER start: prepared environment snapshot has changed since readiness." >&2
  exit 1
fi

# The installed unit must consume the immutable readiness snapshot, never the
# mutable general MarketAI .env file.
ENVIRONMENT_FILES="$(/usr/bin/systemctl show "$SERVICE" --property=EnvironmentFiles --value 2>&1)" || {
  echo "Refusing PAPER start: could not inspect $SERVICE EnvironmentFiles: ${ENVIRONMENT_FILES:-unknown error}." >&2
  exit 1
}
if ! printf '%s\n' "$ENVIRONMENT_FILES" | grep -Fq "$PREPARED_ENV_FILE"; then
  echo "Refusing PAPER start: $SERVICE is not bound to the prepared environment snapshot." >&2
  exit 1
fi

if /usr/bin/systemctl is-active --quiet "$SERVICE"; then
  echo "$SERVICE is already active on prepared SHA $PREPARED_SHA."
  exit 0
fi

sudo /usr/bin/systemctl start "$SERVICE"
if ! /usr/bin/systemctl is-active --quiet "$SERVICE"; then
  echo "$SERVICE did not become active." >&2
  sudo /usr/bin/systemctl status "$SERVICE" --no-pager -l || true
  exit 1
fi

echo "$SERVICE is active on prepared SHA $PREPARED_SHA with the readiness environment snapshot."
