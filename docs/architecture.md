# Architecture

## Purpose

V1 is a permission-gated personal AI agent that runs as one independent installation on either a Mac or a VPS. Each installation owns its own state, local workspace, tools, and credentials. It supports OpenAI for planning and synthesis, DeepSeek through OpenCode for coding work, CLI and later Telegram entry points, Todoist tasks, web research, and approval-gated local actions.

V1 deliberately does not synchronize sessions, files, approvals, or jobs between hosts. Cross-host coordination is a v2 concern.

## Design principles

1. **Policy is deterministic.** Models may request an action but cannot bypass code that checks scope, risk, expiry, and user approval.
2. **One host, one trust boundary.** A deployment uses only the filesystem, credentials, database, and workspace available on its own host.
3. **One workflow graph, specialized model roles.** LangGraph owns state transitions and pauses. PydanticAI provides typed model and tool boundaries inside graph nodes.
4. **Tools return structured evidence.** Every tool result includes identifiers, timestamps, summaries, and any source URLs or changed files needed for verification.
5. **Local-first observability.** Audit records and evaluation fixtures remain under the host owner's control before any external tracing service is introduced.

## Deployment topology

```text
CLI / SSH / optional Telegram
             |
             v
single-host runtime
FastAPI (when needed) + LangGraph + PydanticAI
SQLite state + audit store + policy service
             |
             v
Todoist / web research / local shell / OpenCode
             |
             v
approved host-local workspaces
```

Run the same application on a Mac for local coding and files, or on a VPS for always-on access and Telegram. A Mac may use the CLI and local tools; a VPS may use SSH, an HTTP service, and Telegram. There is no dependency between the two deployments.

SQLite stores sessions, approvals, workflow checkpoints, tool/audit events, task metadata, and memory indexes for one installation. Move to PostgreSQL only when a single host needs concurrent processes or higher availability.

## Application layers

| Layer | Responsibility | Package |
| --- | --- | --- |
| Transport | Normalize CLI, HTTP, and Telegram inputs; render replies and approval prompts. | `api`, `cli` |
| Session | Authenticate the user and create or resume a bounded session. | `core`, `persistence` |
| Policy | Convert requested effects into allow, deny, or approval-required decisions. | `policy` |
| Orchestration | Maintain agent state, route work, checkpoint, pause, resume, and verify results. | `graph` |
| Model workers | Plan, research, and summarize through OpenAI; create typed requests and results. | `models` |
| Tool gateway | Validate parameters, invoke adapters, return structured evidence, and emit audit events. | `tools` |
| Local execution | Run approved shell, workspace, and OpenCode actions on the current host. | `execution` |
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
execute local tool -> verify -> persist evidence -> response
```

The initial graph has a single OpenAI coordinator. It delegates only when a task matches a concrete capability:

- **Research:** call the web-research tool, synthesize findings, and preserve source links.
- **Tasks:** call the Todoist adapter through the tool gateway.
- **Coding:** invoke the host-local OpenCode adapter with DeepSeek and return a summary, diff metadata, changed files, and test results.

This is intentionally not a free-form multi-agent conversation. Graph edges and typed tool contracts make execution inspectable and testable.

## Model boundaries

| Role | Provider | Authority |
| --- | --- | --- |
| Coordinator | OpenAI via PydanticAI | Read context, propose plans, request approved tools, compose final replies. |
| Research worker | OpenAI via PydanticAI | Request web research and cite sources; no direct writes. |
| Coding adapter | OpenCode with DeepSeek | Modify only the current host's approved workspace; report evidence. |

PydanticAI models produce validated request schemas. LangGraph selects the node and manages run state. A model cannot call the filesystem, Todoist, or OpenCode directly; those effects always go through the tool gateway and policy engine.

## Permissions

Every requested effect is assigned a risk level:

| Level | Examples | Default |
| --- | --- | --- |
| `read` | search Todoist, fetch web pages, list approved files | allowed |
| `write` | create/update tasks, edit files in a workspace, create a local repository | approved once per session and scope |
| `risky` | delete files, install packages, run unrestricted shell commands, push Git changes, contact external services | approved per action |

An approval grant records the session, tool, operation, resource pattern, risk level, expiry, and user-visible summary. It is never inferred from a previous session. Grants are checked by the policy layer immediately before every local execution.

### Workspace policy

- New repositories may be created only under the configured agent workspace root, for example `~/agent-workspaces`.
- Existing repositories require an explicit path in the session grant.
- Coding and shell actions run in a Docker sandbox with only the selected workspace mounted.
- Native host execution is not a supported v1 fallback; unavailable Docker causes the action to fail closed.
- Remote pushes, dependency installation, secret access, and destructive commands always require a separate risky-action approval.

## Data model

The initial SQLite schema will contain:

- `sessions` and `conversation_events`
- `approval_grants` and `approval_requests`
- `workflow_runs` and `workflow_checkpoints`
- `tool_calls` and append-only `audit_events`
- `memory_documents`, `memory_chunks`, and `memory_retrievals`
- `evaluation_cases` and `evaluation_runs`

Secrets do not belong in this database or git. Each host uses a permissions-restricted environment file or deployment secret store. Audit records redact tokens, authorization headers, and configured sensitive fields.

## Observability and evaluation

Structured logs and append-only audit events are the initial observability system. Each event contains `run_id`, `session_id`, optional `approval_id`, graph node, model/tool identity, duration, usage/cost when available, outcome, and redacted metadata.

The evaluation suite uses versioned cases with mocked tools for fast deterministic tests and separate integration cases for real providers. The release gate measures:

- zero policy violations or unapproved effects;
- tool-schema and result-schema validity;
- research source presence and URL validity;
- Todoist and local-execution task completion;
- coding test outcomes; and
- latency and cost regression against a recorded baseline.

Raw prompts and tool outputs have configurable retention. Add a hosted tracing product only if local inspection no longer meets debugging needs.

## V2: coordination

Cross-host coordination is intentionally deferred. V2 can introduce a control plane, a private job protocol, host identity, synchronization rules, and a network boundary only after the single-host workflow and permission model have proven useful.
