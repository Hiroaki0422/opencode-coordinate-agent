#!/usr/bin/env bash
set -Eeuo pipefail

SERVICE_NAME="${SERVICE_NAME:-personal-agent-telegram.service}"
SERVICE_USER="${SERVICE_USER:-personal-agent}"
APP_DIR="${APP_DIR:-/opt/personal-agent}"
STATE_DIR="${STATE_DIR:-/var/lib/personal-agent}"
ENV_FILE="${ENV_FILE:-/etc/personal-agent/personal-agent.env}"
UNIT_FILE="${UNIT_FILE:-/etc/systemd/system/${SERVICE_NAME}}"
SANDBOX_IMAGE="${SANDBOX_IMAGE:-personal-agent-sandbox:latest}"
PYTHON_INSTALL_DIR="${PYTHON_INSTALL_DIR:-${APP_DIR}/.uv-python}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

fail() {
    printf 'error: %s\n' "$*" >&2
    exit 1
}

require_absolute_path() {
    local value="$1"
    local name="$2"
    [[ "$value" =~ ^/[A-Za-z0-9._/-]+$ ]] || \
        fail "${name} must be an absolute path containing only safe characters"
}

[[ "${EUID}" -eq 0 ]] || fail "run this installer as root"
[[ "$SERVICE_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || fail "SERVICE_USER is invalid"
require_absolute_path "$APP_DIR" "APP_DIR"
require_absolute_path "$STATE_DIR" "STATE_DIR"
require_absolute_path "$ENV_FILE" "ENV_FILE"
require_absolute_path "$UNIT_FILE" "UNIT_FILE"
require_absolute_path "$PYTHON_INSTALL_DIR" "PYTHON_INSTALL_DIR"
[[ -f "${APP_DIR}/pyproject.toml" ]] || fail "clone the repository at ${APP_DIR} first"
[[ "$(realpath -- "$REPOSITORY_ROOT")" == "$(realpath -- "$APP_DIR")" ]] || \
    fail "run ${APP_DIR}/deploy/install-systemd.sh from the deployment clone"
[[ ! -e "${APP_DIR}/.env" ]] || \
    fail "remove ${APP_DIR}/.env after moving production settings to ${ENV_FILE}"
command -v systemctl >/dev/null || fail "systemctl is unavailable"
command -v docker >/dev/null || fail "Docker CLI is unavailable"
UV_BIN="${UV_BIN:-$(command -v uv || true)}"
if [[ -z "$UV_BIN" && -x /root/.local/bin/uv ]]; then
    UV_BIN=/root/.local/bin/uv
fi
[[ "$UV_BIN" == /* && -x "$UV_BIN" ]] || \
    fail "uv is unavailable; install it in /usr/local/bin or set UV_BIN to an absolute executable path"
getent group docker >/dev/null || fail "the docker group does not exist"

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    useradd \
        --system \
        --home-dir "$STATE_DIR" \
        --create-home \
        --shell /usr/sbin/nologin \
        "$SERVICE_USER"
fi
usermod --append --groups docker "$SERVICE_USER"

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 "$STATE_DIR"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0700 \
    "${STATE_DIR}/data" \
    "${STATE_DIR}/workspaces" \
    "${STATE_DIR}/codex-auth" \
    "${STATE_DIR}/tmp/codex"
install -d -o root -g root -m 0755 "$(dirname -- "$ENV_FILE")"

if [[ ! -f "$ENV_FILE" ]]; then
    sed \
        -e "s|/opt/personal-agent|${APP_DIR}|g" \
        -e "s|/var/lib/personal-agent|${STATE_DIR}|g" \
        "${SCRIPT_DIR}/systemd/personal-agent.env.example" >"$ENV_FILE"
fi
chown root:root "$ENV_FILE"
chmod 0600 "$ENV_FILE"

TEMP_UNIT="$(mktemp)"
trap 'rm -f -- "$TEMP_UNIT"' EXIT
sed \
    -e "s|@SERVICE_USER@|${SERVICE_USER}|g" \
    -e "s|@APP_DIR@|${APP_DIR}|g" \
    -e "s|@STATE_DIR@|${STATE_DIR}|g" \
    -e "s|@ENV_FILE@|${ENV_FILE}|g" \
    "${SCRIPT_DIR}/systemd/personal-agent-telegram.service.in" >"$TEMP_UNIT"
install -o root -g root -m 0644 "$TEMP_UNIT" "$UNIT_FILE"

chown -R root:root "$APP_DIR"
chmod 0755 "$APP_DIR"
(
    cd -- "$APP_DIR"
    export UV_PYTHON_INSTALL_DIR="$PYTHON_INSTALL_DIR"
    "$UV_BIN" venv --clear --managed-python --python 3.12 .venv
    "$UV_BIN" sync \
        --frozen \
        --no-dev \
        --managed-python \
        --python "${APP_DIR}/.venv/bin/python"
    docker build --tag "$SANDBOX_IMAGE" docker/sandbox
)

systemctl daemon-reload
printf 'Installed %s but did not start it.\n' "$SERVICE_NAME"
printf 'Edit %s, configure provider authentication, then run:\n' "$ENV_FILE"
printf '  systemctl enable --now %s\n' "$SERVICE_NAME"
