# Run Locally on a Mac

This guide runs one independent personal-agent installation on a MacBook through the CLI. Telegram,
VPS coordination, and RAG are not required.

## Prerequisites

- Python 3.12 or newer
- `uv`
- Docker Desktop with the Docker daemon running
- An OpenAI API key for the coordinator
- A DeepSeek API key for OpenCode coding tasks

## Install

From the repository root:

```bash
cp .env.example .env
uv sync --all-groups
docker build -t personal-agent-sandbox:latest docker/sandbox
```

The sandbox image contains Python, Git, Node.js, and the pinned OpenCode CLI. OpenCode does not need to
be installed directly on the Mac.

## Configure

Edit `.env` and set at least:

```dotenv
PERSONAL_AGENT_OPENAI__ENABLED=true
PERSONAL_AGENT_OPENAI__API_KEY=your-openai-key
PERSONAL_AGENT_DEEPSEEK__ENABLED=true
PERSONAL_AGENT_DEEPSEEK__API_KEY=your-deepseek-key

PERSONAL_AGENT_COORDINATOR__ENABLED=true
PERSONAL_AGENT_COORDINATOR__MODELS='[{"provider":"openai","model":"gpt-5-mini"}]'

PERSONAL_AGENT_CONVERSATION__MAX_TURNS=20
PERSONAL_AGENT_CONVERSATION__MAX_CONTEXT_CHARS=40000

PERSONAL_AGENT_LOCAL_EXECUTION__ENABLED=true
PERSONAL_AGENT_LOCAL_EXECUTION__WORKSPACE_ROOT=~/agent-workspaces
PERSONAL_AGENT_OPENCODE__ENABLED=true
PERSONAL_AGENT_OPENCODE__MODEL=deepseek/deepseek-chat
```

To let the agent modify an existing repository outside `~/agent-workspaces`, add exact absolute paths:

```dotenv
PERSONAL_AGENT_LOCAL_EXECUTION__REPOSITORY_PATHS='["/Users/your-name/source/project"]'
```

Docker Desktop must be allowed to share those paths. Do not place real secrets in a repository that
the coding worker can mount; `.env` reads and edits are denied, but repository contents are otherwise
inside the coding trust boundary.

### Optional: Use ChatGPT Codex instead of an OpenAI API key

Install a supported Codex CLI, then authenticate using your ChatGPT account:

```bash
codex login --device-auth
codex login status
```

The verified minimum version is `codex-cli 0.144.0-alpha.4`. Configure Codex as the only coordinator:

```dotenv
PERSONAL_AGENT_OPENAI__ENABLED=false
PERSONAL_AGENT_OPENAI__API_KEY=
PERSONAL_AGENT_CODEX_SUBSCRIPTION__ENABLED=true
PERSONAL_AGENT_CODEX_SUBSCRIPTION__MODEL=gpt-5.4
PERSONAL_AGENT_CODEX_SUBSCRIPTION__CODEX_HOME=~/.personal-agent/codex-auth
PERSONAL_AGENT_COORDINATOR__ENABLED=true
PERSONAL_AGENT_COORDINATOR__MODELS='[{"provider":"codex-subscription","model":"gpt-5.4"}]'
```

Or place an API provider after it as an optional fallback. Confirm local authentication without using
subscription tokens:

```bash
uv run personal-agent codex-health
```

Create and authenticate the dedicated credential directory configured above:

```bash
mkdir -p "$HOME/.personal-agent/codex-auth"
CODEX_HOME="$HOME/.personal-agent/codex-auth" codex login --device-auth
```

Codex CLI owns and refreshes its OAuth credentials. The agent does not copy them into `.env`, SQLite,
checkpoints, logs, or commands. If authentication expires, rerun `codex login --device-auth`. Use
`codex logout` to disconnect the local CLI, or disable
`PERSONAL_AGENT_CODEX_SUBSCRIPTION__ENABLED` and restore an API-backed coordinator route.

An authenticated smoke test is intentionally skipped by default because it consumes subscription
allowance. Run it only when desired:

```bash
PERSONAL_AGENT_RUN_CODEX_SMOKE=true \
  uv run pytest tests/integration/test_codex_subscription_smoke.py -q
```

## Start Interactive Chat

Start a persistent terminal conversation:

```bash
uv run personal-agent chat
```

The command creates a bounded session and prints its ID. Every prompt creates a separate durable
workflow run, but recent complete user/assistant turns are supplied to the next request. Resume the
same conversation after restarting the process:

```bash
uv run personal-agent chat --session-id YOUR_SESSION_ID
```

Available commands are:

- `/help` — show commands.
- `/status` — show session expiry and the latest workflow run.
- `/session` — print the current session ID.
- `/history` — show locally stored conversation messages.
- `/clear` — remove conversation messages while retaining workflow and append-only audit records.
- `/new` — create and switch to a new conversation and permission session.
- `/quit` — exit cleanly; `Ctrl+D` also exits and `Ctrl+C` cancels current input or work.

When an action needs approval, the chat displays the paused run ID, tool, operation, resource, effect,
risk, reason, and expiry. Enter `approve` or `deny`; empty or unrelated input never approves an action.
If the process exits while a run is paused, recover through the existing durable commands:

```bash
uv run personal-agent inspect RUN_ID
uv run personal-agent approve RUN_ID  # or: personal-agent deny RUN_ID
```

Conversation messages live in the application SQLite database configured by
`PERSONAL_AGENT_DATABASE_URL`. Only the most recent complete turns fitting both conversation limits
are sent to a model. Configured credentials and authorization strings are redacted before persistence,
but avoid entering unrelated secrets into chat. This history provides short-term conversational
continuity only; it is not document ingestion, semantic search, or RAG.

## Optional: Run the Telegram Bot

Create a bot with [BotFather](https://core.telegram.org/bots/features#botfather), keep its token out of
Git, and send the new bot a `/start` message. Retrieve the pending update to find both identity values:

```bash
read -rs TELEGRAM_BOT_TOKEN
echo
curl --silent --request POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates" | python3 -m json.tool
unset TELEGRAM_BOT_TOKEN
```

In the returned `message`, use `chat.id` as the allowed chat ID and `from.id` as the allowed user ID.
They are often equal in a private chat, but configure and verify both. Add the values to `.env`:

```dotenv
PERSONAL_AGENT_TELEGRAM__ENABLED=true
PERSONAL_AGENT_TELEGRAM__BOT_TOKEN=your-bot-token
PERSONAL_AGENT_TELEGRAM__ALLOWED_CHAT_IDS='[123456789]'
PERSONAL_AGENT_TELEGRAM__ALLOWED_USER_IDS='[123456789]'
```

Start the long-polling process:

```bash
uv run personal-agent telegram
```

This is a foreground process, not an installed service. Closing its SSH session normally stops it.
During VPS testing, keep it attached to a persistent terminal such as `tmux`:

```bash
tmux new -s personal-agent
cd ~/personal-agent
PERSONAL_AGENT_LOG_LEVEL=INFO uv run personal-agent telegram
```

Detach with `Ctrl+B`, then `D`; later inspect it with `tmux attach -t personal-agent`. Production
startup, restart, backup, and recovery use the [single-host VPS deployment](deployment.md) assets.

Only updates matching both allowlists are accepted. `/help`, `/status`, `/session`, `/history`,
`/clear`, `/new`, `/workspace`, `/workspaces`, `/last-operation`, and `/operation` are handled
directly by the authenticated transport. Ordinary messages reuse one durable SQLite
conversation session. The bot shows `Planning…` while the workflow runs and replaces it with the final
response or an approval card containing the tool, operation, resource, effect, risk, reason, and
expiry.

Approve or deny using the inline buttons. Each callback contains a short opaque token; SQLite stores
only its SHA-256 digest, binds it to the exact chat, user, session, and run, and accepts it once before
resuming the LangGraph checkpoint. Expired, reused, wrong-user, and wrong-chat callbacks fail closed.
If a consumed approval cannot resume, the bot includes the run ID so it can be inspected through the
CLI.

Workspace selection is durable per session:

```text
/workspaces
/workspace todo-test
/workspace
```

After a tool attempt, inspect its bounded, redacted SQLite receipt without asking a model to infer
what happened:

```text
/last-operation
/last-operation log
/operation RUN_ID
/operation RUN_ID diff
/operation RUN_ID tests
```

`Show the OpenCode operation log` retrieves the same sanitized receipt. Raw process and service logs
remain administrator-only in journald. Receipts contain bounded worker events, reports, diffs, test
exit codes, expected and observed files, and verification status; they exclude environment values and
test stdout/stderr. No automatic receipt retention is currently configured, so normal SQLite backup
and deletion policy applies.

Requests such as `Can you show me the failure log?` and `Why did OpenCode fail?` retrieve the newest
failed or OpenCode receipt directly. They do not ask the coordinator to search the workspace. A
workspace file listing is not an operation log; `.git` metadata is excluded from normal file-list
results.

Run only one polling process for a bot token. Startup removes any configured webhook because Telegram
does not deliver `getUpdates` while a webhook is active. Stop polling with `Ctrl+C`. To disable mobile
access, stop the process, set `PERSONAL_AGENT_TELEGRAM__ENABLED=false`, and rotate or revoke the bot
token through BotFather when appropriate.

If replies stop, send a brand-new `/help` message first because it does not call a model. If `/help`
fails, check the polling process and Telegram transport; if `/help` works but an ordinary prompt does
not, check coordinator authentication, timeout, and provider logs. Do not call Bot API `getUpdates`
manually while the application poller is active because competing long polls can interrupt one
another. INFO logs distinguish `telegram.poll_started`, `telegram.updates_received`,
`telegram.identity_rejected`, and `telegram.update_failed`.

Test the integration without contacting Telegram:

```bash
uv run pytest -q \
  tests/unit/test_telegram_client.py \
  tests/unit/test_telegram_bot.py \
  tests/integration/test_telegram_state.py
```

For a live smoke test, send `/help`, ask a read-only question, then request a local write or coding task
and confirm that its inline approval describes the exact intended effect before selecting a decision.

## Start a Session

```bash
uv run personal-agent session start
```

Copy the returned `session_id`, then submit a request:

```bash
uv run personal-agent run \
  "Use OpenCode in /absolute/path/to/repository to update app.py. Run pytest -q." \
  --session-id YOUR_SESSION_ID
```

The response pauses with `approval_required` and returns a `run_id`. Inspect and approve it:

```bash
uv run personal-agent inspect RUN_ID
uv run personal-agent approve RUN_ID
```

Use `personal-agent deny RUN_ID` instead if the repository, effect, or task details are incorrect.
Every OpenCode task requires a fresh approval because the container contacts DeepSeek. File writes and
new workspace creation follow their own scoped approval rules.

## Create a New Repository

Ask the coordinator to create it below the configured workspace root:

```bash
uv run personal-agent run \
  "Create a Git workspace named notes-agent, then tell me its path." \
  --session-id YOUR_SESSION_ID
```

After creation, that canonical path becomes the session's active workspace. Later requests may say
`current workspace`; the runtime supplies the stored path as trusted context and canonicalizes the
resource before policy and approval. OpenCode changes remain in place even when expected-file or test
verification fails, and the response distinguishes observed effects from verified completion.

OpenCode never uses the Codex coordinator's temporary request directory as a workspace. If a session
has no active workspace, workspace-dependent actions stop before policy or tool execution and ask you
to create or select one. Check the session deterministically with `/workspace`; use `/workspaces` and
`/workspace <name>` to select an existing repository.

Equivalent host CLI inspection commands are:

```bash
uv run personal-agent workspaces --session-id YOUR_SESSION_ID
uv run personal-agent workspace --session-id YOUR_SESSION_ID --select notes-agent
uv run personal-agent workspace --session-id YOUR_SESSION_ID
uv run personal-agent operation --session-id YOUR_SESSION_ID
uv run personal-agent operation --session-id YOUR_SESSION_ID --run-id RUN_ID --view log
```

## Validate the Installation

```bash
uv run pytest
uv run ruff check .
uv run mypy src/personal_agent
```

If a coding task reports that Docker or the sandbox image is unavailable, confirm Docker Desktop is
running and rebuild the image. If OpenCode rejects the model identifier, list DeepSeek models using the
OpenCode CLI in an explicitly network-enabled disposable container and update
`PERSONAL_AGENT_OPENCODE__MODEL` to the returned `provider/model` value.
