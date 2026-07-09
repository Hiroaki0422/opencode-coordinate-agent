# Personal Agent

A privacy-conscious personal AI agent with a VPS-based control plane and a Mac worker for local execution.

The first release will provide a CLI, Todoist task management, source-based web research, approval-gated local shell access, and delegated coding work through OpenCode with DeepSeek. Telegram and retrieval-augmented memory follow after the core safety and tool paths are proven.

## Architecture

```text
CLI / Telegram / SSH
        |
        v
VPS control plane
FastAPI + LangGraph + PydanticAI + SQLite
        |
        v
Mac worker
OpenCode + local shell + approved workspaces
```

The control plane owns sessions, policy, orchestration, and audit records. The Mac worker only executes signed, scoped jobs. Models propose actions; deterministic policy code authorizes them.

See [the architecture](docs/architecture.md), [the implementation backlog](docs/backlog.md), and [the first architecture decision](docs/decisions/0001-control-plane-worker-boundary.md).

## Repository layout

```text
src/personal_agent/
  api/          HTTP and Telegram adapters
  cli/          Local command-line interface
  core/         Shared contracts and domain types
  graph/        LangGraph state and nodes
  models/       PydanticAI model workers
  persistence/  SQLite repositories and audit storage
  policy/       Approval and capability enforcement
  tools/        Audited tool adapters
  worker/       VPS-to-Mac job protocol and worker runtime
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

The repository is currently an architecture scaffold. The first runnable slice is defined in the P0 backlog.
