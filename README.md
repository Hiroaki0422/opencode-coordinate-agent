# Personal Agent

A privacy-conscious personal AI agent that runs independently on a single Mac or VPS.

The first release provides interactive CLI and Telegram conversations, provider-neutral model fallback, optional ChatGPT Codex subscription coordination, durable approval checkpoints, audited persistence, Todoist operations, source-based DuckDuckGo research, approval-gated Docker execution, and sandboxed OpenCode delegation. Retrieval-augmented memory follows in a later phase.

## Architecture

```text
CLI / Telegram / SSH
        |
        v
Single-host runtime
FastAPI + LangGraph + PydanticAI + SQLite
OpenCode + local shell + approved workspaces
```

Each installation owns its own sessions, policy, orchestration, audit records, and local workspace. Models propose actions; deterministic policy code authorizes them. VPS-to-Mac coordination is deferred to v2.

See [the architecture](docs/architecture.md), [the implementation backlog](docs/backlog.md), and [the first architecture decision](docs/decisions/0001-single-host-v1.md).

For laptop setup, CLI usage, and Telegram configuration, see [Run locally on a Mac](docs/getting-started.md).

## Repository layout

```text
src/personal_agent/
  application/  Transport-neutral runtime and conversation lifecycle
  api/          Future HTTP adapters
  cli/          Local command-line interface
  core/         Shared contracts and domain types
  graph/        LangGraph state and nodes
  models/       PydanticAI model workers
  observability/ Structured logging and secret redaction
  persistence/  SQLite repositories and audit storage
  policy/       Approval and capability enforcement
  tools/        Audited tool adapters
  execution/    Host-local shell, workspace, and OpenCode execution
  telegram/     Authenticated Bot API polling and approval UI
docs/           Architecture, decisions, and backlog
config/         Checked-in example configuration only
tests/          Unit, integration, and evaluation fixtures
```

## Development

The project targets Python 3.12+ and uses `uv` for dependency management.

```bash
cp .env.example .env
uv sync --all-groups
uv run pytest
uv run ruff check .
```

Set only the integrations you intend to use. Startup validation requires credentials only for integrations whose `enabled` flag is `true`.

P0 through the Telegram integration in P4 are complete. Build the sandbox image with `docker build -t personal-agent-sandbox:latest docker/sandbox`, configure either API-backed models or the optional Codex subscription coordinator, and enable local execution/OpenCode as needed. Start an interactive conversation with `uv run personal-agent chat`, run mobile polling with `uv run personal-agent telegram`, or use `personal-agent run` plus `personal-agent approve` or `personal-agent deny` for machine-readable automation. Docker networking stays disabled except for individually approved actions such as a DeepSeek-backed OpenCode task.
