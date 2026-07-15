#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-personal-agent-telegram.service}"
APP_DIR="${APP_DIR:-/opt/personal-agent}"
STATE_DIR="${STATE_DIR:-/var/lib/personal-agent}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/personal-agent}"
LEAVE_STOPPED=false

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf 'Usage: %s [--leave-stopped]\n' "$(basename -- "$0")"
}

for argument in "$@"; do
    case "$argument" in
        --leave-stopped) LEAVE_STOPPED=true ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; fail "unknown argument: ${argument}" ;;
    esac
done

[[ "${EUID}" -eq 0 ]] || fail "run this backup as root"
[[ "$STATE_DIR" == /* && "$BACKUP_DIR" == /* ]] || fail "state and backup paths must be absolute"
[[ -d "$STATE_DIR" ]] || fail "state directory does not exist: ${STATE_DIR}"
command -v systemctl >/dev/null || fail "systemctl is unavailable"
command -v tar >/dev/null || fail "tar is unavailable"
command -v sha256sum >/dev/null || fail "sha256sum is unavailable"

install -d -o root -g root -m 0700 "$BACKUP_DIR"
WAS_ACTIVE=false
if systemctl is-active --quiet "$SERVICE_NAME"; then
    WAS_ACTIVE=true
    systemctl stop "$SERVICE_NAME"
fi

restart_if_needed() {
    if [[ "$WAS_ACTIVE" == true && "$LEAVE_STOPPED" == false ]]; then
        systemctl start "$SERVICE_NAME" || \
            printf 'warning: failed to restart %s\n' "$SERVICE_NAME" >&2
    fi
}
trap restart_if_needed EXIT

TIMESTAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
HOST_ID="$(hostname | tr -cs 'A-Za-z0-9._-' '_')"
BASE_NAME="personal-agent-${HOST_ID}-${TIMESTAMP}"
ARCHIVE="${BACKUP_DIR}/${BASE_NAME}.tar.gz"
PARTIAL="${ARCHIVE}.partial"
METADATA="${BACKUP_DIR}/${BASE_NAME}.metadata"
REVISION="unknown"
if [[ -d "${APP_DIR}/.git" ]]; then
    REVISION="$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null || printf 'unknown')"
fi

rm -f -- "$PARTIAL"
tar \
    --create \
    --gzip \
    --one-file-system \
    --file "$PARTIAL" \
    --directory "$STATE_DIR" \
    --exclude='./codex-auth' \
    --exclude='./tmp' \
    .
mv -- "$PARTIAL" "$ARCHIVE"
(
    cd -- "$BACKUP_DIR"
    sha256sum "$(basename -- "$ARCHIVE")" >"$(basename -- "$ARCHIVE").sha256"
)
cat >"$METADATA" <<EOF
created_at=${TIMESTAMP}
hostname=${HOST_ID}
service=${SERVICE_NAME}
application_revision=${REVISION}
state_directory=${STATE_DIR}
excluded=codex-auth,tmp,environment-file
EOF
chmod 0600 "$ARCHIVE" "${ARCHIVE}.sha256" "$METADATA"

printf 'Backup created: %s\n' "$ARCHIVE"
printf 'OAuth credentials and the environment file were intentionally excluded.\n'
