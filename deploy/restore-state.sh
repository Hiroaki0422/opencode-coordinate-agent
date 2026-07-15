#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-personal-agent-telegram.service}"
SERVICE_USER="${SERVICE_USER:-personal-agent}"
APP_DIR="${APP_DIR:-/opt/personal-agent}"
STATE_DIR="${STATE_DIR:-/var/lib/personal-agent}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/personal-agent}"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

usage() {
    printf 'Usage: %s /absolute/path/to/personal-agent-backup.tar.gz\n' "$(basename -- "$0")"
}

[[ $# -eq 1 ]] || { usage >&2; exit 2; }
[[ "${EUID}" -eq 0 ]] || fail "run this restore as root"
ARCHIVE="$(realpath -- "$1")"
[[ "$ARCHIVE" == /* && -f "$ARCHIVE" ]] || fail "backup archive does not exist"
CHECKSUM="${ARCHIVE}.sha256"
[[ -f "$CHECKSUM" ]] || fail "checksum file does not exist: ${CHECKSUM}"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "deployment Python is unavailable"
id "$SERVICE_USER" >/dev/null 2>&1 || fail "service user does not exist"

(
    cd -- "$(dirname -- "$ARCHIVE")"
    sha256sum --check "$(basename -- "$CHECKSUM")"
)
while IFS= read -r entry; do
    case "$entry" in
        /*|../*|*/../*|*/..) fail "archive contains an unsafe path: ${entry}" ;;
    esac
done < <(tar --list --gzip --file "$ARCHIVE")

WAS_ACTIVE=false
if systemctl is-active --quiet "$SERVICE_NAME"; then
    WAS_ACTIVE=true
    systemctl stop "$SERVICE_NAME"
fi

STATE_PARENT="$(dirname -- "$STATE_DIR")"
STAGING="$(mktemp -d "${STATE_PARENT}/.personal-agent-restore.XXXXXX")"
ROLLBACK="$(mktemp -d "${STATE_PARENT}/.personal-agent-rollback.XXXXXX")"
STATE_SWITCHED=false
cleanup() {
    local exit_status=$?
    if [[ "$exit_status" -ne 0 && "$STATE_SWITCHED" == true ]]; then
        systemctl stop "$SERVICE_NAME" >/dev/null 2>&1 || true
        rm -rf -- "${STATE_DIR}/data" "${STATE_DIR}/workspaces"
        for component in data workspaces; do
            if [[ -e "${ROLLBACK}/${component}" ]]; then
                mv -- "${ROLLBACK}/${component}" "${STATE_DIR}/${component}"
            fi
        done
        chown -R "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"
    fi
    rm -rf -- "$STAGING" "$ROLLBACK"
    if [[ "$WAS_ACTIVE" == true ]] && ! systemctl is-active --quiet "$SERVICE_NAME"; then
        systemctl start "$SERVICE_NAME" >/dev/null 2>&1 || true
    fi
    return "$exit_status"
}
trap cleanup EXIT

tar --extract --gzip --file "$ARCHIVE" --directory "$STAGING"
[[ -d "${STAGING}/data" ]] || fail "backup contains no data directory"
install -d -m 0700 "${STAGING}/workspaces"

"${APP_DIR}/.venv/bin/python" - "${STAGING}/data" <<'PY'
from pathlib import Path
import sqlite3
import sys

data_directory = Path(sys.argv[1])
databases = sorted(data_directory.glob("*.sqlite3"))
if not databases:
    raise SystemExit("backup contains no SQLite databases")
for database in databases:
    with sqlite3.connect(database) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise SystemExit(f"SQLite integrity check failed: {database.name}")
PY

TIMESTAMP="$(date -u +'%Y%m%dT%H%M%SZ')"
ROLLBACK_ARCHIVE="${BACKUP_DIR}/pre-restore-${TIMESTAMP}.tar.gz"
install -d -o root -g root -m 0700 "$BACKUP_DIR"
tar \
    --create \
    --gzip \
    --one-file-system \
    --file "$ROLLBACK_ARCHIVE" \
    --directory "$STATE_DIR" \
    --exclude='./codex-auth' \
    --exclude='./tmp' \
    .
(
    cd -- "$BACKUP_DIR"
    sha256sum "$(basename -- "$ROLLBACK_ARCHIVE")" \
        >"$(basename -- "$ROLLBACK_ARCHIVE").sha256"
)
chmod 0600 "$ROLLBACK_ARCHIVE" "${ROLLBACK_ARCHIVE}.sha256"

STATE_SWITCHED=true
for component in data workspaces; do
    if [[ -e "${STATE_DIR}/${component}" ]]; then
        mv -- "${STATE_DIR}/${component}" "${ROLLBACK}/${component}"
    fi
    mv -- "${STAGING}/${component}" "${STATE_DIR}/${component}"
done
chown -R "$SERVICE_USER:$SERVICE_USER" "${STATE_DIR}/data" "${STATE_DIR}/workspaces"
chmod 0700 "${STATE_DIR}/data" "${STATE_DIR}/workspaces"

if [[ "$WAS_ACTIVE" == true ]]; then
    if ! systemctl start "$SERVICE_NAME" || ! systemctl is-active --quiet "$SERVICE_NAME"; then
        fail "restored service failed; previous state was put back"
    fi
fi

printf 'State restored from: %s\n' "$ARCHIVE"
printf 'Pre-restore rollback archive: %s\n' "$ROLLBACK_ARCHIVE"
printf 'Existing Codex OAuth state and environment secrets were preserved.\n'
