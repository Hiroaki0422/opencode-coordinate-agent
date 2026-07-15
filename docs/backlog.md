# Implementation Backlog

Items are ordered by dependency and user value. An item is complete only when its acceptance criteria and focused tests pass.

## Development status

Further roadmap development is paused while the current Telegram release undergoes rigorous user
testing. New defects, usability changes, and reliability improvements are triaged in
[`user-testing-backlog.md`](user-testing-backlog.md) and take priority over the remaining roadmap.

The unfinished single-host deployment assets are paused rather than cancelled. Native RAG development
has moved out of this repository's active roadmap: a separate production RAG application will be built
in another session, and any personal-agent integration will be planned only after that application's
interface and security model are known.

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

- [x] Verify and document the supported Codex CLI contract.
  - Pin and test a minimum Codex CLI version, non-interactive command, JSON event format, structured
    output support, read-only sandbox mode, timeout behavior, authentication status, and exit codes.
  - Detect a missing executable, missing ChatGPT login, expired authorization, subscription exhaustion,
    rate limits, malformed output, and unsupported CLI versions with distinct sanitized errors.
  - Verified against `codex-cli 0.144.0-alpha.4`; see ADR 0006 and offline contract tests.
- [x] Add `CodexSubscriptionSettings` and startup validation.
  - Configure enablement, executable path, model, timeout, working directory, and maximum response size.
  - Enabling the provider requires a healthy Codex CLI login but never requires an OpenAI API key.
  - Credentials remain owned by the Codex CLI under its user configuration directory; they are never
    copied into `.env`, SQLite, checkpoints, audit payloads, Docker arguments, or application logs.
- [x] Implement a restricted Codex CLI process adapter.
  - Invoke the executable with an argument vector rather than a host shell and use a clean temporary
    working directory with no project repository, secrets, or writable application paths mounted.
  - Disable or deny autonomous filesystem writes, shell execution, network tools, MCP servers, plugins,
    and repository discovery so Codex acts only as an underlying reasoning provider.
  - Apply process timeout, output-size, environment allowlist, cancellation, and child-process cleanup.
- [x] Implement a typed `CodexSubscriptionCoordinator`.
  - Conform to the existing `Coordinator` protocol for both `decide` and evidence-based `compose` calls.
  - Request JSON matching `CoordinatorDecision` or `GroundedResponse`, validate it with Pydantic, and
    perform a bounded corrective retry when the CLI returns invalid structured output.
  - Treat all Codex text as untrusted model output; deterministic policy and the tool gateway retain all
    action authority exactly as they do for API-backed coordinators.
- [x] Extend provider-neutral coordinator routing.
  - Support ordered routes containing both PydanticAI API models and the Codex subscription coordinator.
  - Permit Codex subscription to be primary, fallback, or the only configured provider.
  - Fall back only for classified provider failures; never fall through after a policy denial, tool
    failure, malformed user request, or partially completed external effect.
- [x] Add local observability without credential exposure.
  - Audit provider name, CLI version, model, duration, exit class, retry count, response digest, and
    fallback decision while excluding prompts, raw OAuth data, access tokens, and refresh tokens.
  - Surface actionable login instructions when reauthentication is required and preserve the original
    provider failure when every configured fallback is exhausted.
- [x] Add deterministic and opt-in integration tests.
  - Mock the subprocess for successful decisions, synthesis, invalid JSON, corrective retry, timeout,
    missing login, exhausted subscription, rate limit, cancellation, and ordered fallback behavior.
  - Verify that a model-proposed action still pauses at the existing policy node and cannot invoke tools
    or write files directly through the Codex process.
  - Keep CI independent of a real ChatGPT account; provide a separately marked local smoke test that
    consumes subscription allowance only when the user explicitly enables it.
- [x] Update setup, security, and recovery documentation.
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

## P3.5.1 — Interactive multi-turn CLI

This phase adds a persistent terminal conversation while preserving the existing non-interactive JSON
commands for scripts. A conversation session provides bounded context across turns, but it does not
replace approval grants, LangGraph checkpoints, or future RAG-based long-term memory.

- [x] Extract reusable agent runtime services from the Typer commands.
  - Own database, coordinator, graph, policy, gateway, and verifier lifecycles outside the terminal UI.
  - Expose typed operations to create or resume a session, submit a turn, inspect a run, and resume an
    approval so CLI and later Telegram transports share the same orchestration path.
- [x] Add durable conversation messages and repository methods.
  - Store user and assistant messages with session ID, workflow run ID, role, timestamp, and a bounded
    content field in SQLite.
  - Commit messages only at defined lifecycle points and avoid persisting secrets, raw credentials,
    unbounded command output, or internal model reasoning.
- [x] Build bounded provider-neutral conversation context.
  - Load the most recent complete turns within configurable turn and character limits.
  - Pass the same normalized history to API-backed and Codex subscription coordinators without changing
    deterministic policy or tool authority.
  - Treat stored tool and research content as untrusted input and clearly separate it from system rules.
- [x] Add the `personal-agent chat` REPL.
  - Create a session when omitted, optionally resume an existing session, and keep runtime resources open
    until the user exits.
  - Accept repeated prompts, render concise human-readable responses, and keep `personal-agent run` JSON
    output backward compatible.
- [x] Handle approvals inside the interactive loop.
  - Show the requested tool, operation, resource, effect, risk, reason, and expiry before asking the user.
  - Accept explicit approve or deny input, resume the same durable workflow checkpoint, and never treat
    ordinary chat text or an empty response as approval.
- [x] Add interactive session commands and terminal behavior.
  - Support `/help`, `/status`, `/session`, `/history`, `/clear`, `/new`, and `/quit`.
  - Handle `Ctrl+C` as cancellation of the current input or operation and `Ctrl+D` as a clean exit without
    corrupting the session or leaving child processes running.
- [x] Add observability and redaction for conversation turns.
  - Audit session, run, provider, duration, outcome, approval decisions, and bounded content digests while
    excluding raw prompts, model responses, credentials, and sensitive tool output from logs.
- [x] Add deterministic interactive CLI tests.
  - Cover multi-turn references, history truncation, restart persistence, `/clear`, new sessions, inline
    approval and denial, cancellation, provider fallback, failed tools, and duplicate-effect prevention.
  - Test the REPL with mocked terminal input and coordinators so CI needs no network, Docker, provider
    credentials, or ChatGPT subscription usage.
- [x] Document interactive usage and recovery.
  - Add setup examples, session-resume commands, approval behavior, history limits, local data location,
    clearing history, and the distinction between conversation context and future RAG memory.

### P3.5.1 acceptance criteria

- `personal-agent chat` supports multiple turns in one bounded session and can resume that session later.
- Follow-up requests can reference recent turns while context remains within configured size limits.
- Interactive approvals use the existing policy and checkpoint path and cannot bypass explicit consent.
- Conversation history remains local, bounded, redacted from logs, and independently clearable.
- Existing non-interactive CLI commands and machine-readable JSON output remain backward compatible.
- Offline tests cover terminal interaction, persistence, truncation, approvals, cancellation, and restart.

## P4 — Telegram and operational polish

- [x] Add Telegram long-polling authentication and user/chat allowlists.
  - Rejects messages outside the configured account and routes approvals through one-time action tokens.
- [x] Render approvals and local-execution progress for Telegram.
  - Shows the tool, resource, effect, reason, expiry, and approve/deny controls.
- [ ] Add single-host deployment assets.
  - Documents system-service setup, backup, recovery, upgrade, and secret rotation.
  - **Status:** Paused during Telegram user testing.

## P5 — Memory and RAG (moved to separate development)

The production RAG application will be designed and implemented separately. This repository will not
add native ingestion, chunking, or vector retrieval during the current development pause. A future
session may define an authenticated, source-preserving tool or API integration after the external RAG
application is stable.

## Deferred intentionally

- Scheduled routines and autonomous background work.
- Browser control and computer-use actions.
- Multi-agent swarms or unconstrained agent handoffs.
- Automatic remote pushes, external messages, or payment actions.
- Hosted observability beyond optional future tracing export.
- VPS-to-Mac coordination, job dispatch, and state synchronization.
