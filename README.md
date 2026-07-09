# Personal Agent

A privacy-conscious personal AI agent that runs independently on a single Mac or VPS.

The first release will provide a CLI, Todoist task management, source-based web research, approval-gated local shell access, and delegated coding work through OpenCode with DeepSeek. Telegram and retrieval-augmented memory follow after the core safety and tool paths are proven.

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
  execution/    Host-local shell, workspace, and OpenCode execution
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

P0 implementation is underway. Typed settings and audited SQLite persistence are complete; the remaining safe-core work is tracked in the backlog.
