#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-personal-agent-telegram.service}"
SERVICE_USER="${SERVICE_USER:-personal-agent}"
APP_DIR="${APP_DIR:-/opt/personal-agent}"
STATE_DIR="${STATE_DIR:-/var/lib/personal-agent}"
ENV_FILE="${ENV_FILE:-/etc/personal-agent/personal-agent.env}"
BRANCH="${BRANCH:-main}"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

[[ "${EUID}" -eq 0 ]] || fail "run this upgrade as root"
[[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "BRANCH is invalid"
[[ -d "${APP_DIR}/.git" ]] || fail "application directory is not a Git clone"
[[ -z "$(git -C "$APP_DIR" status --porcelain)" ]] || fail "deployment clone is not clean"

PREVIOUS_REVISION="$(git -C "$APP_DIR" rev-parse HEAD)"
printf 'Current revision: %s\n' "$PREVIOUS_REVISION"

upgrade_failed() {
    printf 'Upgrade failed. Previous revision was %s.\n' "$PREVIOUS_REVISION" >&2
    printf 'Review docs/deployment.md before rollback; no destructive Git reset was attempted.\n' >&2
    systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
}
trap upgrade_failed ERR

APP_DIR="$APP_DIR" \
STATE_DIR="$STATE_DIR" \
SERVICE_NAME="$SERVICE_NAME" \
"${APP_DIR}/deploy/backup-state.sh" --leave-stopped

git -C "$APP_DIR" fetch --prune origin "$BRANCH"
git -C "$APP_DIR" merge --ff-only "origin/${BRANCH}"
APP_DIR="$APP_DIR" \
STATE_DIR="$STATE_DIR" \
SERVICE_NAME="$SERVICE_NAME" \
SERVICE_USER="$SERVICE_USER" \
ENV_FILE="$ENV_FILE" \
"${APP_DIR}/deploy/install-systemd.sh"

printf '%s\n' "$PREVIOUS_REVISION" >"${STATE_DIR}/previous-deployed-revision"
chown "$SERVICE_USER:$SERVICE_USER" "${STATE_DIR}/previous-deployed-revision"
systemctl start "$SERVICE_NAME"
systemctl is-active --quiet "$SERVICE_NAME" || fail "service did not become active"
trap - ERR

printf 'Upgrade complete: %s\n' "$(git -C "$APP_DIR" rev-parse HEAD)"
