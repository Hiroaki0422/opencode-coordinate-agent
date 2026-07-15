# Architecture

## Purpose

V1 is a permission-gated personal AI agent that runs as one independent installation on either a Mac or a VPS. Each installation owns its own state, local workspace, tools, and credentials. It supports an ordered, provider-neutral model route for planning and synthesis, DeepSeek through OpenCode for coding work, CLI and Telegram entry points, Todoist tasks, web research, and approval-gated local actions.

V1 deliberately does not synchronize sessions, files, approvals, or jobs between hosts. Cross-host coordination is a v2 concern.

## Design principles

1. **Policy is deterministic.** Models may request an action but cannot bypass code that checks scope, risk, expiry, and user approval.
2. **One host, one trust boundary.** A deployment uses only the filesystem, credentials, database, and workspace available on its own host.
3. **One workflow graph, specialized model roles.** LangGraph owns state transitions and pauses. PydanticAI provides typed model and tool boundaries inside graph nodes.
4. **Tools return structured evidence.** Every tool result includes identifiers, timestamps, summaries, and any source URLs or changed files needed for verification.
5. **Local-first observability.** Audit records and evaluation fixtures remain under the host owner's control before any external tracing service is introduced.

## Deployment topology

```text
CLI / SSH / optional Telegram polling
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

SQLite stores sessions, conversation messages, approvals, workflow runs, and audit events for one installation. LangGraph uses a separate SQLite checkpoint file so interrupted runs can resume across process restarts. Move to PostgreSQL only when a single host needs concurrent processes or higher availability.

## Application layers

| Layer | Responsibility | Package |
| --- | --- | --- |
| Transport | Normalize CLI and Telegram inputs; render replies and approval prompts. | `cli`, `telegram` |
| Application runtime | Own shared dependency lifecycles and expose typed session, run, approval, and inspection operations to transports. | `application` |
| Session | Authenticate the user and create or resume a bounded session. | `core`, `persistence` |
| Policy | Convert requested effects into allow, deny, or approval-required decisions. | `policy` |
| Orchestration | Maintain agent state, route work, checkpoint, pause, resume, and verify results. | `graph` |
| Model workers | Route across configured providers; create typed requests and results. | `models` |
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
execute registered tool -> verify -> persist evidence -> response
```

The initial graph has one coordinator backed by an ordered model fallback route. It delegates only when a task matches a concrete capability:

- **Research:** call the web-research tool, synthesize findings, and preserve source links.
- **Tasks:** call the Todoist adapter through the tool gateway.
- **Coding:** invoke the host-local OpenCode adapter with DeepSeek and return a summary, diff metadata, changed files, and test results.

This is intentionally not a free-form multi-agent conversation. Graph edges and typed tool contracts make execution inspectable and testable. The tool gateway audits the start and outcome of every call. A verification node rejects unsupported success claims before rendering the response.

## P1 tool integrations

### Todoist

The `TaskProvider` protocol separates task semantics from Todoist. The v1 Todoist adapter uses the current `/api/v1` endpoints, follows cursor pagination, deduplicates records by ID, and sends an idempotency request ID with mutations. Read operations require `read` risk; create, update, and complete operations require a policy-approved write action.

### Web research

The `SearchProvider` protocol and ordered `SearchRouter` keep research independent from the reasoning model. DuckDuckGo is the first no-key, best-effort adapter through the unofficial `ddgs` package. Other search providers can be registered later without changing graph nodes.

Search results are fetched separately over HTTP. The fetcher accepts only public HTTP/HTTPS URLs, rejects literal private or reserved addresses, limits redirects, content types, bytes, and extracted text, and removes script/style content. Retrieved text is wrapped as untrusted web content before model synthesis.

Research responses are rendered only when the synthesis cites source identifiers that exist in the retrieved evidence. The final response separates model synthesis from retrieved source URLs.

## P2 local execution

The `local_execution` adapter exposes Docker health checks, workspace creation, file listing and
reading, file writing, and argument-vector command execution. It accepts only workspaces beneath
the configured root and rejects absolute, parent-relative, missing, or symlink-escaping file paths.
Repository creation uses a validated single-directory name and initializes Git inside the sandbox.

Every action runs in a short-lived container with only one workspace mounted. Containers use a
read-only root filesystem, dropped Linux capabilities, `no-new-privileges`, process, memory, and CPU
limits, a constrained temporary filesystem, and the host caller's numeric user identity. Read tools
mount the workspace read-only; writes mount it read-write. The application invokes the Docker CLI
directly without a host shell and never falls back to native execution.

Container networking is `none` by default. A command may request ordinary Docker bridge networking
only when its action is classified `risky`, which forces an individual approval rather than creating
a reusable session grant. Package managers, destructive commands, remote pushes, and common network
download commands are also rejected unless the action has risky approval. Audit completion events
record the command vector, exit code, output hashes, truncation status, and network state; raw command
output remains in the transient structured result rather than the audit event.

## P3 coding delegation

The `opencode/code_task` adapter accepts a typed task, acceptance criteria, expected relative file
paths, and constrained test command vectors. It resolves the action resource to either a repository
below the managed workspace root or one exact path in the configured repository allowlist. The path
must be a Git repository and must match the resource approved by the outer policy layer.

OpenCode runs non-interactively inside the same hardened image with a pinned version and a DeepSeek
model profile. Runtime configuration is supplied through `OPENCODE_CONFIG_CONTENT`: repository reads
and edits are allowed, while shell, subagent, external-directory, web, install, branch, destructive,
commit, and push capabilities are denied. Only the DeepSeek key and isolated `/tmp` configuration
paths enter the container environment. The key is inherited through the Docker process environment,
not included in command arguments or audit payloads.

Coding delegation uses network access to contact DeepSeek, so every task is a `risky` action requiring
individual approval. After execution, the adapter captures Git status, diff summary, bounded diff,
changed files, the OpenCode report, and separately executed offline test results. Success requires the
repository snapshot to change, all expected files to appear in Git evidence, and every requested test
to pass. Existing dirty state is reported explicitly.

## P3.5 Codex subscription coordinator

The optional `codex-subscription` coordinator uses the official Codex CLI's ChatGPT login instead of
an OpenAI API key. It is a reasoning provider only: models may return a typed `ActionRequest`, but the
existing LangGraph policy, approval, tool gateway, and verification nodes retain all authority.

Each request runs `codex exec` without a host shell in a unique empty temporary directory. The command
uses read-only sandboxing, ephemeral sessions, ignored user configuration and rules, a JSON output
schema, JSONL events, and a bounded final-response file. Shell and unified execution, apps, browser and
computer use, plugins, hooks, image generation, multi-agent behavior, workspace dependencies, and MCP
dependency installation are explicitly disabled. Prompts travel over stdin rather than command-line
arguments, and the subprocess receives only an allowlist of path, home, temporary-directory, and TLS
environment variables. API keys and unrelated host secrets are excluded.

Codex OAuth state uses a dedicated configured `CODEX_HOME` rather than the active IDE's default
credential directory. This prevents independent CLI processes from racing on the same refresh token.
The setting stores only the directory path; Codex CLI continues to own the token files. Coordinator
output uses strict wire schemas with `additionalProperties: false`; arbitrary tool arguments cross the
structured-output boundary as a JSON string and are parsed and validated before becoming an
`ActionRequest`.

The coordinator validates responses as `CoordinatorDecision` or `GroundedResponse` and permits one
bounded corrective retry by default. Ordered routing may mix Codex subscription and API-backed model
groups. Fallback happens only for classified provider failures such as missing login, expired OAuth,
subscription exhaustion, rate limits, timeout, malformed output, or retryable model HTTP failures;
policy denials and application validation failures never trigger provider fallback.

Local structured events record provider, CLI version, model, duration, sanitized exit class, retry
count, response digest, and fallback selection without prompts or credentials. CLI startup probes the
configured mixed route; `personal-agent codex-health` performs an explicit version and login check
without consuming model tokens.

## P3.5.1 interactive conversation

`personal-agent chat` keeps one application runtime open while accepting repeated terminal prompts.
Every prompt still creates a separate durable workflow run and LangGraph checkpoint; the conversation
session only groups those runs and supplies bounded context. Inline approvals display the run, tool,
operation, resource, effect, risk, policy reason, and expiry, and accept only explicit approve or deny
input before resuming the same checkpoint.

SQLite stores user messages when a run is created and stores assistant messages only after a terminal
response. Paused or cancelled runs therefore never become complete history turns. Before a new run,
the runtime selects the newest complete user/assistant pairs within configured turn and character
limits and serializes the same normalized history for API-backed and Codex subscription coordinators.
History is labeled as untrusted context rather than system instructions. Configured credentials and
authorization strings are redacted before message text or workflow summaries enter SQLite.

`/history` renders local messages, `/clear` removes conversation messages while retaining workflow and
append-only audit records, and `/new` creates a fresh permission and conversation session. This is
short-term conversational continuity, not semantic retrieval: P5 RAG remains responsible for finding
relevant information across documents or older knowledge by source.

## P4 Telegram transport

`personal-agent telegram` runs one asynchronous Bot API long-polling loop on either host. A small
`httpx` client disables webhooks at startup, requests only message and callback-query updates, uses a
positive long-poll timeout, and advances the update offset. Telegram HTTP failures are sanitized so
the bot token and provider response body do not enter application errors. Transient polling failures
back off and retry; the process exits cleanly on cancellation.

Authentication requires an exact match in both configured chat and user allowlists. Rejected identity
metadata is audited without forwarding message text to the model. Accepted `(chat_id, user_id)` pairs
map to one durable application session, so conversation history and permission expiry behave the same
as the CLI across process restarts. Claimed update IDs are stored before processing to prevent replay.

The transport uses the shared `AgentRuntime`; it has no independent model, policy, or tool path. It
renders planning progress, bounded final messages, session commands, and approval cards. Approval
buttons carry an opaque callback token below Telegram's 64-byte limit. Only a SHA-256 token digest is
stored, together with its chat, user, session, run, and expiry. Consumption is an atomic one-time state
change before the existing LangGraph `resume` operation, so duplicated, expired, or cross-identity
callbacks cannot authorize an effect. A post-consumption resume failure reports the durable run ID for
CLI recovery rather than minting a second authorization.

## Model boundaries

| Role | Provider | Authority |
| --- | --- | --- |
| Coordinator | Ordered providers via PydanticAI | Read context, propose plans, request approved tools, compose final replies. |
| Research worker | Configured provider route | Request web research and cite sources; no direct writes. |
| Coding adapter | OpenCode with DeepSeek | Modify only the current host's approved workspace; report evidence. |

PydanticAI models produce validated request schemas. Provider builders are registered by name, and an ordered fallback route moves to the next model when a provider API fails. LangGraph selects the node and manages run state. A model cannot call the filesystem, Todoist, or OpenCode directly; those effects always go through the tool gateway and policy engine.

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

The current application SQLite schema contains:

- `sessions` and bounded `conversation_messages`
- `approval_grants` and `approval_requests`
- `workflow_runs`
- `telegram_conversations`, `telegram_action_tokens`, and claimed `telegram_updates`
- append-only `audit_events`

LangGraph workflow checkpoints remain in the separately configured checkpoint SQLite database.
Memory documents, chunks, retrieval records, and durable evaluation results remain future additions.

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
