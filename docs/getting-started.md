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

After that workflow completes, start a second request naming the returned path and describing the code
task, expected files, acceptance criteria, and tests.

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
