# Implementation Backlog

Items are ordered by dependency and user value. An item is complete only when its acceptance criteria and focused tests pass.

## P0 — Safe core loop

### Foundation

- [x] Add settings models, environment validation, and example configuration.
  - Supports portable single-host settings and rejects missing required secrets at startup.
- [x] Create SQLite migrations and repositories for sessions, approvals, workflow runs, and audit events.
  - Creates a fresh database and records an immutable audit event transactionally with each state-changing request.
- [x] Add structured logging with redaction.
  - Never emits tokens, authorization headers, or configured secret fields.
- [x] Establish test fixtures, linting, typing, and CI.
  - Unit tests run offline; formatting, linting, and strict type checks run in CI.

### Policy and orchestration

- [x] Implement capability requests, approvals, expiry, and revocation.
  - A write request succeeds only with a matching active session grant; risky requests always create a new approval request.
- [x] Create the LangGraph state, checkpoint store, and pause/resume approval node.
  - A paused run resumes from its persisted checkpoint without repeating a completed effect.
- [x] Add the provider-neutral coordinator through PydanticAI.
  - Produces typed plans and tool requests, supports ordered provider fallbacks, and cannot invoke a tool outside the gateway.
- [x] Add a CLI transport.
  - Supports starting a session, submitting a request, viewing a pending approval, approving/denying it, and inspecting a run.

## P1 — Useful cloud tools

- [ ] Implement the Todoist `TaskProvider` and adapter.
  - Supports list, create, update, complete, and project lookup; writes require a matching session approval.
- [ ] Implement web search, fetch, extraction, and citation contracts.
  - Research replies retain source URLs and distinguish retrieved facts from the model's synthesis.
- [ ] Add response verification nodes.
  - Task responses include Todoist identifiers; research responses include sources; failed actions explain the failure without claiming success.
- [ ] Add P0/P1 evaluation cases.
  - Covers ordinary actions, expired grants, wrong resource scopes, duplicate execution after resume, and prompt injection in retrieved content.

## P2 — Local execution

- [ ] Add the Docker sandbox runtime and health check.
  - Fails closed when Docker is unavailable and never falls back to native host execution.
- [ ] Add a constrained filesystem and shell adapter.
  - Mounts only granted paths into Docker; each command, exit code, and output digest is audited.
- [ ] Add sandboxed workspace creation.
  - Creates new repositories only below the configured workspace root and returns the created path.

## P3 — OpenCode coding delegation

- [ ] Add the OpenCode adapter and DeepSeek profile.
  - Starts only for an approved host-local workspace and passes a structured task contract.
- [ ] Capture coding evidence.
  - Returns changed files, diff summary, command/test results, and a concise execution report.
- [ ] Add named-repository access grants.
  - Requires an explicit repository path; branch changes, remote pushes, installs, and destructive commands remain risky actions.
- [ ] Add code-task evaluations.
  - Tests repository creation, workspace escape rejection, failed tests, and requested-change verification.

## P4 — Telegram and operational polish

- [ ] Add Telegram long-polling authentication and user/chat allowlists.
  - Rejects messages outside the configured account and routes approvals through one-time action tokens.
- [ ] Render approvals and local-execution progress for Telegram.
  - Shows the tool, resource, effect, reason, expiry, and approve/deny controls.
- [ ] Add single-host deployment assets.
  - Documents system-service setup, backup, recovery, upgrade, and secret rotation.

## P5 — Memory and RAG

- [ ] Add disk document ingestion with provenance and hashes.
  - Each indexed chunk links to the original file, version/hash, and access scope.
- [ ] Implement retrieval with source-aware answers.
  - Retrieval respects workspace permissions and answers cite source file paths/chunks.
- [ ] Add vector retrieval only after baseline keyword/metadata search is measured.
  - Compare recall, latency, and answer usefulness against the non-vector baseline.

## Deferred intentionally

- Scheduled routines and autonomous background work.
- Browser control and computer-use actions.
- Multi-agent swarms or unconstrained agent handoffs.
- Automatic remote pushes, external messages, or payment actions.
- Hosted observability beyond optional future tracing export.
- VPS-to-Mac coordination, job dispatch, and state synchronization.
