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

- [x] Implement the Todoist `TaskProvider` and adapter.
  - Supports list, create, update, complete, and project lookup; writes require a matching session approval.
- [x] Implement web search, fetch, extraction, and citation contracts.
  - Research replies retain source URLs and distinguish retrieved facts from the model's synthesis.
- [x] Add response verification nodes.
  - Task responses include Todoist identifiers; research responses include sources; failed actions explain the failure without claiming success.
- [x] Add P0/P1 evaluation cases.
  - Covers ordinary actions, expired grants, wrong resource scopes, duplicate execution after resume, and prompt injection in retrieved content.

## P2 — Local execution

- [x] Add the Docker sandbox runtime and health check.
  - Fails closed when Docker is unavailable and never falls back to native host execution.
- [x] Add a constrained filesystem and shell adapter.
  - Mounts only granted paths into Docker; each command, exit code, and output digest is audited.
- [x] Add sandboxed workspace creation.
  - Creates new repositories only below the configured workspace root and returns the created path.

## P3 — OpenCode coding delegation

- [x] Add the OpenCode adapter and DeepSeek profile.
  - Starts only for an approved host-local workspace and passes a structured task contract.
- [x] Capture coding evidence.
  - Returns changed files, diff summary, command/test results, and a concise execution report.
- [x] Add named-repository access grants.
  - Requires an explicit repository path; branch changes, remote pushes, installs, and destructive commands remain risky actions.
- [x] Add code-task evaluations.
  - Tests repository creation, workspace escape rejection, failed tests, and requested-change verification.

## P3.5 — Optional Codex subscription provider

This phase adds ChatGPT/Codex subscription usage as an optional coordinator provider without replacing
the existing API-key providers. The first implementation invokes the official Codex CLI and lets that
CLI own OAuth login, credential storage, refresh, subscription limits, and reauthentication. Directly
implementing or copying OAuth tokens is intentionally out of scope for the initial version.

- [ ] Verify and document the supported Codex CLI contract.
  - Pin and test a minimum Codex CLI version, non-interactive command, JSON event format, structured
    output support, read-only sandbox mode, timeout behavior, authentication status, and exit codes.
  - Detect a missing executable, missing ChatGPT login, expired authorization, subscription exhaustion,
    rate limits, malformed output, and unsupported CLI versions with distinct sanitized errors.
- [ ] Add `CodexSubscriptionSettings` and startup validation.
  - Configure enablement, executable path, model, timeout, working directory, and maximum response size.
  - Enabling the provider requires a healthy Codex CLI login but never requires an OpenAI API key.
  - Credentials remain owned by the Codex CLI under its user configuration directory; they are never
    copied into `.env`, SQLite, checkpoints, audit payloads, Docker arguments, or application logs.
- [ ] Implement a restricted Codex CLI process adapter.
  - Invoke the executable with an argument vector rather than a host shell and use a clean temporary
    working directory with no project repository, secrets, or writable application paths mounted.
  - Disable or deny autonomous filesystem writes, shell execution, network tools, MCP servers, plugins,
    and repository discovery so Codex acts only as an underlying reasoning provider.
  - Apply process timeout, output-size, environment allowlist, cancellation, and child-process cleanup.
- [ ] Implement a typed `CodexSubscriptionCoordinator`.
  - Conform to the existing `Coordinator` protocol for both `decide` and evidence-based `compose` calls.
  - Request JSON matching `CoordinatorDecision` or `GroundedResponse`, validate it with Pydantic, and
    perform a bounded corrective retry when the CLI returns invalid structured output.
  - Treat all Codex text as untrusted model output; deterministic policy and the tool gateway retain all
    action authority exactly as they do for API-backed coordinators.
- [ ] Extend provider-neutral coordinator routing.
  - Support ordered routes containing both PydanticAI API models and the Codex subscription coordinator.
  - Permit Codex subscription to be primary, fallback, or the only configured provider.
  - Fall back only for classified provider failures; never fall through after a policy denial, tool
    failure, malformed user request, or partially completed external effect.
- [ ] Add local observability without credential exposure.
  - Audit provider name, CLI version, model, duration, exit class, retry count, response digest, and
    fallback decision while excluding prompts, raw OAuth data, access tokens, and refresh tokens.
  - Surface actionable login instructions when reauthentication is required and preserve the original
    provider failure when every configured fallback is exhausted.
- [ ] Add deterministic and opt-in integration tests.
  - Mock the subprocess for successful decisions, synthesis, invalid JSON, corrective retry, timeout,
    missing login, exhausted subscription, rate limit, cancellation, and ordered fallback behavior.
  - Verify that a model-proposed action still pauses at the existing policy node and cannot invoke tools
    or write files directly through the Codex process.
  - Keep CI independent of a real ChatGPT account; provide a separately marked local smoke test that
    consumes subscription allowance only when the user explicitly enables it.
- [ ] Update setup, security, and recovery documentation.
  - Explain installing Codex CLI, signing in with ChatGPT, checking login health, selecting route order,
    logging out, revoking access, and returning to API-key providers.
  - Document that ChatGPT subscription limits and availability differ from API billing and that this
    optional provider depends on the supported behavior of the installed Codex CLI.

### P3.5 acceptance criteria

- The agent can run with Codex subscription as its only coordinator without an OpenAI API key.
- Codex cannot directly execute tools or modify the user's repository while serving as coordinator.
- Typed decisions and grounded responses pass the same validation and policy path as existing models.
- Missing, expired, limited, or malformed Codex responses fail safely and use configured fallbacks.
- No OAuth credential or token value appears in logs, audit events, databases, checkpoints, or commands.
- Offline tests pass without Codex credentials, and the optional authenticated smoke test is documented.

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
