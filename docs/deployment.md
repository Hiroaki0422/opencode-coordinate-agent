# Single-Host VPS Deployment

This guide deploys one independent Personal Agent Telegram process as a hardened `systemd` service on
a Linux VPS. It replaces tmux for normal operation. It does not coordinate with another host and does
not install an HTTP endpoint.

## Supported topology

The deployment assets assume:

- a systemd-based Linux VPS;
- one dedicated `personal-agent` service account;
- Docker Engine with a root-owned daemon and a `docker` group;
- `uv`, Git, and Docker available to the administrator;
- application code at `/opt/personal-agent`;
- secrets at `/etc/personal-agent/personal-agent.env`;
- SQLite state and managed workspaces below `/var/lib/personal-agent`; and
- journald for service logs.

The service account's Docker-group membership is effectively root-equivalent because Docker can mount
host paths and start privileged containers. The application still applies its own workspace and
container restrictions, but the VPS administrator must treat this account as privileged.

## Filesystem boundary

| Path | Owner and mode | Purpose |
| --- | --- | --- |
| `/opt/personal-agent` | `root:root`, readable | Versioned application code and virtual environment |
| `/etc/personal-agent/personal-agent.env` | `root:root`, `0600` | Provider, Telegram, and operational settings |
| `/var/lib/personal-agent/data` | service user, `0700` | Application and LangGraph SQLite databases |
| `/var/lib/personal-agent/workspaces` | service user, `0700` | Managed repositories and files |
| `/var/lib/personal-agent/codex-auth` | service user, `0700` | Codex-owned OAuth state |
| `/var/lib/personal-agent/tmp` | service user, `0700` | Restricted provider temporary files |
| `/var/backups/personal-agent` | `root:root`, `0700` | Local state archives and checksums |

Code is read-only to the service. The unit grants write access only to the state directory, gives the
process no Linux capabilities, uses a private `/tmp`, and applies systemd filesystem, kernel, device,
address-family, and privilege hardening.

## Prerequisites

Install Docker, Git, `uv`, and optionally a globally accessible Codex CLI. The uv standalone
installer defaults to the current user's `~/.local/bin`, which may be omitted from sudo's restricted
`PATH`. For predictable administrator and upgrade behavior, install uv system-wide:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

Confirm:

```bash
systemctl is-active docker
getent group docker
uv --version
git --version
```

Push all desired application changes before deployment. For a private repository, configure a
read-only deploy key or another administrator-owned Git credential. Do not place Git credentials in
the service environment file.

## Install

Stop the temporary tmux poller so only one process can call Telegram `getUpdates`:

```bash
tmux attach -t personal-agent
# Press Ctrl+C after attaching, then exit the tmux shell.
pgrep -af 'personal-agent telegram'
```

Clone a clean deployment copy outside `/root` and `/home` because the service protects home
directories:

```bash
sudo git clone YOUR_REPOSITORY_URL /opt/personal-agent
cd /opt/personal-agent
sudo ./deploy/install-systemd.sh
```

If the prompt already shows a root shell and uv was installed at `/root/.local/bin/uv`, either omit
the redundant `sudo` or rely on the installer's root-local fallback:

```bash
./deploy/install-systemd.sh
```

The installer:

1. refuses unsafe or unexpected paths;
2. creates the dedicated account and state directories;
3. grants Docker-group membership;
4. writes a root-only environment template if one does not exist;
5. renders and installs the systemd unit;
6. creates the locked production virtual environment;
7. builds the Docker sandbox image; and
8. reloads systemd without enabling or starting an unconfigured service.

The installer keeps uv-managed Python below `/opt/personal-agent/.uv-python`. This is intentional:
the hardened service cannot execute an interpreter installed below `/root` or `/home`.

The installer refuses an application-local `.env`. Production configuration belongs only in the
root-owned systemd environment file.

## Configure

Edit the installed template:

```bash
sudoedit /etc/personal-agent/personal-agent.env
sudo chmod 0600 /etc/personal-agent/personal-agent.env
sudo chown root:root /etc/personal-agent/personal-agent.env
```

Systemd environment files do not perform shell expansion. Keep absolute paths and surround JSON
objects containing double quotes with single quotes.

At minimum, configure the Telegram token and exact identities:

```dotenv
PERSONAL_AGENT_TELEGRAM__ENABLED=true
PERSONAL_AGENT_TELEGRAM__BOT_TOKEN=replace-with-rotated-token
PERSONAL_AGENT_TELEGRAM__ALLOWED_CHAT_IDS=[8601057133]
PERSONAL_AGENT_TELEGRAM__ALLOWED_USER_IDS=[8601057133]
```

Configure one coordinator. For Codex subscription:

```dotenv
PERSONAL_AGENT_CODEX_SUBSCRIPTION__ENABLED=true
PERSONAL_AGENT_CODEX_SUBSCRIPTION__EXECUTABLE=/usr/local/bin/codex
PERSONAL_AGENT_COORDINATOR__ENABLED=true
PERSONAL_AGENT_COORDINATOR__MODELS='[{"provider":"codex-subscription","model":"gpt-5.4"}]'
```

Authentication owned by `root` in its default Codex home does not authenticate the isolated service
account. First locate the installed executable:

```bash
command -v codex
readlink -f "$(command -v codex)"
```

The configured executable must be reachable outside `/root` and `/home` because the systemd unit
protects those directories. Install or expose Codex at a system path such as `/usr/local/bin/codex`,
then set `PERSONAL_AGENT_CODEX_SUBSCRIPTION__EXECUTABLE` to that exact path.

Alternatively, install the official standalone CLI into the service user's state directory and set
the executable to `/var/lib/personal-agent/.local/bin/codex`:

```bash
sudo -u personal-agent env HOME=/var/lib/personal-agent \
  sh -c 'curl -fsSL https://chatgpt.com/codex/install.sh | sh'
```

Check the isolated authentication state before starting a new device login:

```bash
sudo -u personal-agent env \
  HOME=/var/lib/personal-agent \
  CODEX_HOME=/var/lib/personal-agent/codex-auth \
  /var/lib/personal-agent/.local/bin/codex login status
```

If that command succeeds, skip device authentication. A successful login under `/root/.codex` or
another administrator-owned `CODEX_HOME` does not apply to this service account.

Authenticate as the service user so the active administrator account and service never share or race
on one OAuth directory:

```bash
sudo -u personal-agent env \
  HOME=/var/lib/personal-agent \
  CODEX_HOME=/var/lib/personal-agent/codex-auth \
  /var/lib/personal-agent/.local/bin/codex login --device-auth
```

For API-backed providers, enable the provider, place its key in the environment file, and configure
the ordered model route. Enable DeepSeek separately when OpenCode is enabled.

Confirm the service account can reach Docker:

```bash
sudo -u personal-agent docker version
```

## Start and verify

Enable startup at boot and start the Telegram transport:

```bash
sudo systemctl enable --now personal-agent-telegram.service
sudo systemctl status personal-agent-telegram.service --no-pager
sudo journalctl -u personal-agent-telegram.service -n 100 --no-pager
```

Send a new `/help` message through Telegram. Then verify restart behavior:

```bash
sudo systemctl restart personal-agent-telegram.service
sudo systemctl is-active personal-agent-telegram.service
```

Useful operations:

```bash
sudo systemctl stop personal-agent-telegram.service
sudo systemctl start personal-agent-telegram.service
sudo journalctl -u personal-agent-telegram.service --since today
sudo journalctl -u personal-agent-telegram.service --follow
sudo systemctl cat personal-agent-telegram.service
```

Do not run manual `getUpdates` requests while the service is active.

## Back up

Create a consistent backup:

```bash
cd /opt/personal-agent
sudo ./deploy/backup-state.sh
```

The script briefly stops an active service, archives state and workspaces, writes a SHA-256 checksum
and metadata file, then restores the previous service state. It deliberately excludes:

- `/etc/personal-agent/personal-agent.env`;
- Codex OAuth credentials; and
- temporary provider files.

The archive still contains conversations, audits, approvals, and workspace files. Copy it and its
checksum to encrypted off-host storage. Back up the environment file separately using an encrypted
secret-management process. Never commit either backup.

No automatic retention or deletion is performed. Review backups manually before removing old copies.

## Restore

Restore only a trusted archive and its adjacent `.sha256` file:

```bash
cd /opt/personal-agent
sudo ./deploy/restore-state.sh \
  /var/backups/personal-agent/personal-agent-HOST-TIMESTAMP.tar.gz
```

The restore utility:

1. validates the checksum and rejects unsafe archive paths;
2. stops an active service;
3. extracts into a same-filesystem staging directory;
4. runs SQLite integrity checks before switching state;
5. creates a pre-restore rollback archive;
6. preserves the current environment file and Codex OAuth state;
7. restores ownership and restrictive directory modes; and
8. puts the previous state back if the restored service cannot start.

For disaster recovery on a new VPS, install the same application revision first, configure fresh
secrets, authenticate Codex again, copy the archive and checksum, and then run restore.

## Upgrade

The deployment clone must be clean. Upgrade only after the desired revision has passed CI and has been
pushed to the configured branch:

```bash
cd /opt/personal-agent
sudo BRANCH=main ./deploy/upgrade-systemd.sh
```

The upgrade utility creates a stopped-service backup, fetches the selected branch, permits only a
fast-forward merge, reinstalls locked dependencies and the unit, rebuilds the sandbox image, and starts
the service. Database migrations run during application startup. The previous Git revision is stored
at `/var/lib/personal-agent/previous-deployed-revision`.

Review after every upgrade:

```bash
sudo systemctl status personal-agent-telegram.service --no-pager
sudo journalctl -u personal-agent-telegram.service -n 100 --no-pager
sudo -u personal-agent git -C /var/lib/personal-agent/workspaces status 2>/dev/null || true
```

The final Git command is only illustrative; each workspace is its own repository and should be
inspected individually.

## Roll back

Upgrade failures do not run `git reset` automatically. To restore the earlier application revision:

```bash
PREVIOUS="$(sudo cat /var/lib/personal-agent/previous-deployed-revision)"
sudo systemctl stop personal-agent-telegram.service
sudo git -C /opt/personal-agent switch --detach "$PREVIOUS"
cd /opt/personal-agent
sudo ./deploy/install-systemd.sh
sudo systemctl start personal-agent-telegram.service
```

If the old code cannot open a database migrated by the new version, restore the backup printed by the
upgrade command. After diagnosing the release, return the deployment clone to `main` before the next
upgrade:

```bash
sudo git -C /opt/personal-agent switch main
```

## Rotate secrets

Use `sudoedit` so new values are not placed in shell history. For each provider:

1. create the replacement credential at the provider;
2. update `/etc/personal-agent/personal-agent.env`;
3. restart the service and verify Telegram `/help` plus one provider-backed request; and
4. revoke the old credential only after the replacement works.

For Telegram, use BotFather to revoke and regenerate the bot token. For Todoist, OpenAI, and DeepSeek,
follow the provider's token-revocation procedure. Never print a credential in `systemctl status`,
process arguments, issue reports, or chat.

For Codex, stop the service, re-run device login as the service user, verify login status in the same
`CODEX_HOME`, and restart:

```bash
sudo systemctl stop personal-agent-telegram.service
sudo -u personal-agent env \
  HOME=/var/lib/personal-agent \
  CODEX_HOME=/var/lib/personal-agent/codex-auth \
  /usr/local/bin/codex login --device-auth
sudo systemctl start personal-agent-telegram.service
```

## Troubleshooting

### Restart loop

```bash
sudo systemctl reset-failed personal-agent-telegram.service
sudo journalctl -u personal-agent-telegram.service -n 200 --no-pager
```

Common causes are an empty Telegram token, incorrect JSON in the environment file, unavailable model
authentication, a Codex executable inaccessible to the service user, or Docker-group membership not
yet applied.

An exit status of `203/EXEC` with `Permission denied` can mean the virtual environment points to a
uv-managed Python under `/root`, which `ProtectHome=true` blocks. Confirm with:

```bash
head -n 1 /opt/personal-agent/.venv/bin/personal-agent
readlink -f /opt/personal-agent/.venv/bin/python
findmnt -no TARGET,OPTIONS /opt
```

Pull the installer fix and rerun `./deploy/install-systemd.sh` to recreate `.venv` against the
service-readable Python stored below `/opt/personal-agent/.uv-python`. If `/opt` itself is mounted
with `noexec`, move the application to an executable filesystem rather than weakening the unit.

### Telegram receives no reply

Confirm exactly one poller exists, send `/help`, and inspect:

```bash
pgrep -af 'personal-agent telegram'
sudo journalctl -u personal-agent-telegram.service --since '-10 minutes'
```

`/help` does not invoke a model. If it works while ordinary prompts fail, diagnose the coordinator or
tool request instead of Telegram polling.

### Docker action fails

```bash
sudo -u personal-agent docker version
sudo docker image inspect personal-agent-sandbox:latest
```

Do not weaken the systemd unit or mount arbitrary host paths to bypass workspace policy. Add an exact
repository path to production settings only after reviewing its trust boundary and systemd write
access requirements.
