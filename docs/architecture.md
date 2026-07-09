# Architecture

## Purpose

The agent coordinates personal work across a VPS and a Mac without granting a language model unrestricted access to either machine. It must support OpenAI for planning and synthesis, DeepSeek through OpenCode for coding work, CLI and Telegram entry points, Todoist tasks, web research, and approval-gated local actions.

## Design principles

1. **Policy is deterministic.** Models may request an action but cannot bypass code that checks scope, risk, expiry, and user approval.
2. **The VPS coordinates; the Mac executes locally.** The VPS is the durable, always-on system. The Mac is a narrowly scoped execution worker.
3. **One workflow graph, specialized workers.** LangGraph owns state transitions and pauses. PydanticAI provides typed model and tool boundaries inside graph nodes.
4. **Tools return structured evidence.** Every tool result includes identifiers, timestamps, summaries, and any source URLs or changed files needed for verification.
5. **Local-first observability.** Audit records and evaluation fixtures live in project-controlled storage before any external tracing service is introduced.

## Deployment topology

```text
                    public HTTPS
Telegram webhook --------------------> VPS control plane
                                         |
CLI over SSH --------------------------+-- FastAPI ingress
                                         |   LangGraph runtime
                                         |   SQLite state + audit store
                                         |   approval service
                                         |
                                  authenticated job channel
                                         |
                                         v
                                  Mac worker process
                                  OpenCode / shell / files
                                  only approved workspaces
```

The Mac worker maintains an outbound authenticated connection to the VPS or polls for jobs. It never exposes a public service. Tailscale is used for private administrative access and can also protect the worker channel.

SQLite is appropriate for the single VPS control plane in v1. It holds sessions, approvals, workflow checkpoints, tool/audit events, task metadata, and memory indexes. The Mac remains the source of truth for local files and workspaces. Move to PostgreSQL only if concurrent processes or availability requirements exceed SQLite's single-writer model.

## Application layers

| Layer | Responsibility | Package |
| --- | --- | --- |
| Transport | Normalize CLI, HTTP, and Telegram inputs; render replies and approval prompts. | `api`, `cli` |
| Session | Authenticate the user and create or resume a bounded session. | `core`, `persistence` |
| Policy | Convert requested effects into allow, deny, or approval-required decisions. | `policy` |
| Orchestration | Maintain agent state, route work, checkpoint, pause, resume, and verify results. | `graph` |
| Model workers | Plan, research, and summarize through OpenAI; create typed requests and results. | `models` |
| Tool gateway | Validate parameters, invoke adapters, return structured evidence, and emit audit events. | `tools` |
| Execution worker | Accept scoped VPS jobs and run local shell/OpenCode work inside allowed workspaces. | `worker` |
| Persistence | Store durable domain data, checkpoints, audit events, and evaluation run results. | `persistence` |

## LangGraph workflow

```text
ingress -> session -> classify -> plan -> policy check
                                      |          |
                                      |          +-> denied -> response
                                      v
                               approval required
                                      |
                                pause / resume
                                      v
execute tool or worker job -> verify -> persist evidence -> response
```

The initial graph has a single OpenAI coordinator. It delegates only when a task matches a concrete capability:

- **Research:** call the web-research tool, synthesize findings, and preserve source links.
- **Tasks:** call the Todoist adapter through the tool gateway.
- **Coding:** create a `WorkerJob` for OpenCode; OpenCode uses DeepSeek and returns a summary, diff metadata, changed files, and test results.

This is intentionally not a free-form multi-agent conversation. Graph edges and typed job contracts make delegation inspectable and testable.

## Model boundaries

| Role | Provider | Authority |
| --- | --- | --- |
| Coordinator | OpenAI via PydanticAI | Read context, propose plans, request approved tools, compose final replies. |
| Research worker | OpenAI via PydanticAI | Request web research and cite sources; no direct writes. |
| Coding worker | OpenCode with DeepSeek | Modify only the workspace included in a signed job; report evidence. |

PydanticAI models produce validated request schemas. LangGraph selects the node and manages run state. A model cannot call the filesystem, Todoist, or OpenCode directly; those effects always go through the tool gateway and policy engine.

## Permissions

Every requested effect is assigned a risk level:

| Level | Examples | Default |
| --- | --- | --- |
| `read` | search Todoist, fetch web pages, list approved files | allowed |
| `write` | create/update tasks, edit files in a workspace, create a local repository | approved once per session and scope |
| `risky` | delete files, install packages, run unrestricted shell commands, push Git changes, contact external services | approved per action |

An approval grant records the session, tool, operation, resource pattern, risk level, expiry, and user-visible summary. It is never inferred from a previous session. Grants are checked by the policy layer immediately before execution and again by the Mac worker for local jobs.

### Workspace policy

- New repositories may be created only under the configured agent workspace root, for example `~/agent-workspaces`.
- Existing repositories require an explicit path in the session grant.
- Coding jobs run in an isolated sandbox with only the selected workspace mounted.
- Remote pushes, dependency installation, secret access, and destructive commands always require a separate risky-action approval.

## Data model

The initial SQLite schema will contain:

- `sessions` and `conversation_events`
- `approval_grants` and `approval_requests`
- `workflow_runs` and `workflow_checkpoints`
- `tool_calls` and append-only `audit_events`
- `worker_jobs` and `worker_results`
- `memory_documents`, `memory_chunks`, and `memory_retrievals`
- `evaluation_cases` and `evaluation_runs`

Secrets do not belong in this database or git. The VPS uses a permissions-restricted environment file or deployment secret store; the Mac worker uses its local secure secret mechanism. Audit records redact tokens, authorization headers, and configured sensitive fields.

## Observability and evaluation

Structured logs and append-only audit events are the initial observability system. Each event contains `run_id`, `session_id`, optional `approval_id`, graph node, model/tool identity, duration, usage/cost when available, outcome, and redacted metadata.

The evaluation suite uses versioned cases with mocked tools for fast deterministic tests and separate integration cases for real providers. The release gate measures:

- zero policy violations or unapproved effects;
- tool-schema and result-schema validity;
- research source presence and URL validity;
- Todoist and worker task completion;
- coding test outcomes; and
- latency and cost regression against a recorded baseline.

Raw prompts and tool outputs have configurable retention. Add a hosted tracing product only if local inspection no longer meets debugging needs.
