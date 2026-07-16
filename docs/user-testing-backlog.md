# Telegram User-Testing Backlog

This is the active work queue while the implementation roadmap is paused. Only changes supported by
real Telegram usage, reproducible defects, security findings, or clear usability friction should be
added here. Roadmap features and RAG development remain outside this queue.

## Priority rules

| Priority | Meaning | Examples |
| --- | --- | --- |
| P0 | Stop testing and fix immediately. | Unapproved effects, credential exposure, data loss, or cross-user access. |
| P1 | Blocks a core workflow. | Bot cannot start, poll, reply, approve, deny, or recover a run. |
| P2 | Material reliability or usability issue. | Confusing status, restart problems, duplicate messages, or poor error recovery. |
| P3 | Non-blocking polish. | Wording, formatting, command discoverability, or minor documentation improvements. |

## Incoming feedback

Add each new observation here before implementation:

- [ ] `UT-006` — Telegram polling stopped responding during VPS testing and the foreground process
  lifecycle was not obvious.
  - **Priority:** P1
  - **Environment:** VPS through Telegram
  - **Scenario:** Start `personal-agent telegram`, leave it running, and later send another message
  - **Expected:** Clear process status and actionable evidence when polling is no longer active
  - **Actual:** Telegram messages stopped receiving responses
  - **Reproduction:** Pending process and INFO-log evidence
  - **Evidence:** Collect process state and sanitized polling logs
  - **Decision:** Systemd deployment accepted as the lifecycle fix; keep open until restart, idle,
    network-failure, and boot behavior pass on the VPS

- [ ] `UT-XXX` — Short problem statement.
  - **Priority:** P0, P1, P2, or P3
  - **Environment:** Host, OS, installation method, and tested revision
  - **Scenario:** Commands and Telegram actions performed
  - **Expected:** Intended behavior
  - **Actual:** Observed behavior
  - **Reproduction:** Always, intermittent, or not yet reproduced
  - **Evidence:** Sanitized logs, run/session/update IDs, or screenshots; never credentials
  - **Decision:** Pending, accepted, rejected, or needs more evidence

## Testing checklist

- [ ] Verify first-time BotFather setup, `.env` loading, allowlists, and `/start` response.
- [ ] Verify long polling survives idle periods, transient network failures, and process restarts.
- [ ] Verify multi-turn conversation continuity, `/new`, `/history`, and `/clear` through Telegram.
- [ ] Verify approve, deny, expiry, duplicate callback, and wrong-identity approval behavior.
- [ ] Verify progress and final responses for research, Todoist, local execution, and coding tasks.
- [ ] Verify provider timeout, fallback, Codex reauthentication, and actionable mobile errors.
- [ ] Verify long-response splitting, unsupported messages, malformed commands, and Unicode input.
- [ ] Verify SQLite and LangGraph recovery after cancellation or an unexpected process exit.
- [ ] Verify unauthorized users receive no agent access and generate sanitized audit evidence.
- [ ] Record VPS resource use, response latency, and operational friction during normal usage.

## Suggested scripted actions

Run these in order against disposable workspaces and test-only external records. Record any mismatch
under **Incoming feedback** with sanitized evidence.

### Conversation and Telegram

1. Send `My temporary project codename is Kestrel.` and then `What is my project codename?`
   - Expected: answers `Kestrel` from bounded conversation history without an approval.
2. Run `/session`, `/status`, `/history`, `/clear`, and `/history`.
   - Expected: commands render useful state; history is empty after clearing.
3. Run `/new`, then ask for the previous codename.
   - Expected: a new session ID and no reliance on the old conversation.
4. Ask for a response longer than 5,000 characters containing Unicode such as `日本語` and emoji.
   - Expected: multiple ordered Telegram messages without truncation errors or broken encoding.

### Workspace and files

1. Send `Create a Git workspace named telegram-sandbox and return its absolute path.`
   - Expected: one scoped write approval and a Git repository below the configured workspace root.
2. Send `In <returned-path>, create notes.txt containing exactly: alpha beta gamma`.
   - Expected: approval, verified write, and no claim of success if persisted bytes differ.
3. Send `Read notes.txt from <returned-path> and show the exact content.`
   - Expected: `alpha beta gamma` with no approval because this is a read.
4. Send `Replace notes.txt in <returned-path> with exactly: delta` and read it again.
   - Expected: valid session-scoped write authorization behavior and exact replacement content.
5. Send `List files in <returned-path>.`
   - Expected: repository files are listed from the read-only sandbox mount.
6. Send `Read ../outside.txt from <returned-path>.`
   - Expected: fail closed with a path-escape error and no host file content.
7. Send `Create a file in /tmp/outside-agent-workspace.`
   - Expected: fail because the target is outside the configured workspace root.

### Approval behavior

1. Request a harmless write, inspect the approval details, and select **Deny**.
   - Expected: no file change and a clear denial response.
2. Approve one harmless write and try to reuse the same Telegram callback.
   - Expected: the token is accepted once only; duplicate use fails closed.
3. Leave an approval untouched beyond `APPROVAL_TTL_MINUTES`, then select **Approve**.
   - Expected: expiry alert and no effect.
4. Run `/new` after receiving a write grant, then request the same write in the new session.
   - Expected: a fresh approval; grants do not cross sessions.

### Sandboxed commands

1. Send `In <returned-path>, run python --version without network access.`
   - Expected: individual risky-action approval and bounded command output.
2. Deny a command request and verify that no command result is claimed.
3. Request an offline command that exits nonzero: `python -c "raise SystemExit(7)"`.
   - Expected: reported exit failure rather than success.
4. Request a network-disabled connection to `https://example.com` from the disposable workspace.
   - Expected: risky approval followed by network failure while Docker networking is `none`.

### Web research and task management

1. Ask `Research the current Python release using web search and include source links.`
   - Expected: no approval, a grounded synthesis, and valid source URLs.
2. If Todoist is enabled, list tasks in a test project.
   - Expected: no approval and returned Todoist identifiers.
3. Create a clearly named disposable Todoist task, deny once, then request it again and approve.
   - Expected: no task after denial; exactly one task after approval.

### Coding delegation

1. In a disposable Git workspace, request a one-file program with explicit acceptance criteria and a
   small offline test command.
   - Expected: individual risky approval, changed-file evidence, diff summary, and passing tests.
2. Request a coding change with a deliberately failing test.
   - Expected: changes may be reported, but the agent must not claim verified success.
3. Request a remote push, package installation, branch change, or destructive command.
   - Expected: denied inside the restricted coding adapter or separately classified as risky; no silent
     external effect.

### Recovery and access control

1. Stop the Telegram process during idle polling, restart it, and send `/status`.
   - Expected: polling resumes and existing SQLite state remains available.
2. Stop after an approval card appears, restart, and inspect the durable run through the CLI.
   - Expected: the run remains inspectable and resumable without repeating a completed effect.
3. From a non-allowlisted Telegram account, send `/start` and ordinary text.
   - Expected: no agent access and a sanitized `telegram.identity_rejected` audit event.
4. Temporarily interrupt outbound network access and restore it.
   - Expected: warning logs, polling retry with backoff, and automatic recovery without duplicate work.

## Accepted changes

Move an incoming item here only after its behavior and scope are understood. Every accepted change
must identify focused tests and documentation updates before implementation begins.

### Implementation sequence

1. Fix OpenCode change detection and truthful result rendering (`UT-008`).
2. Persist and expose sanitized operation receipts (`UT-009`).
3. Add durable active-workspace context and commands (`UT-003`).
4. Add a Codex-equivalent personal workspace access profile (`UT-011`).
5. Generate coordinator capabilities from registered adapters (`UT-010`).
6. Add the typed planner-worker-reviewer coordination loop (`UT-012`).
7. Run the integrated Telegram scenarios and update user documentation.

### Target operating model

The personal-agent profile should match the practical autonomy of Codex `workspace-write` with
on-request approvals: it may read, edit, and run routine commands inside the active workspace, while
network access, paths outside configured roots, destructive commands, installations, pushes, and
credential access remain explicit approval boundaries. The coordinator remains unable to mutate the
host directly; all effects continue through deterministic tools and policy checks.

| Requirement | Target behavior | Backlog items |
| --- | --- | --- |
| At least Codex-level access | One active workspace with autonomous reads, edits, and routine commands plus approval for boundary crossings | `UT-003`, `UT-011` |
| Read OpenCode output and logs | Persist bounded, redacted worker events and expose them through authenticated commands and a read-only agent tool | `UT-009` |
| View OpenCode-produced files | Record exact changed paths, activate that workspace, and support deterministic list/read/diff follow-ups | `UT-003`, `UT-008`, `UT-009` |
| Codex plans and OpenCode implements | Codex produces a typed plan, OpenCode/DeepSeek edits, deterministic checks run, and Codex reviews bounded evidence | `UT-012` |

- [x] `UT-008` — Distinguish OpenCode effects from verification and detect untracked-file changes.
  - **Priority:** P1
  - **Problem:** OpenCode created `todo.py`, but an expected-file mismatch produced only
    `requested file changes could not be verified`; a later turn therefore denied that the file was
    known to exist. The current snapshot also cannot detect content-only changes to an already
    untracked file because it hashes only Git status and tracked diff output.
  - **Design:**
    - Capture bounded before/after workspace manifests containing relative path, size, and SHA-256
      for regular files while excluding `.git` and rejecting symlink escapes.
    - Preserve separate fields for `effect_observed`, `requested_change_verified`, test status,
      expected files, observed changed files, and a stable verification-reason code.
    - Render partial outcomes truthfully: for example, `OpenCode created todo.py, but expected
      files app.py and README.md were not observed.`
    - Keep changes in place when verification fails and explicitly say that no rollback occurred.
    - Continue failing closed on no changes, failed tests, path escapes, oversized evidence, and
      provider errors.
  - **Focused tests:** New untracked file, content-only edit to an untracked file, expected-file
    mismatch, no-op provider response, failed tests, symlink escape, and bounded manifest limits.
  - **Documentation:** Explain effect-versus-verification status and retained unverified changes in
    the coding-delegation testing guide.

- [x] `UT-009` — Provide session-scoped, sanitized operation receipts instead of inaccessible raw
  logs.
  - **Priority:** P2
  - **Problem:** After a tool result, the user cannot ask what happened. The coordinator has only the
    prior rendered sentence, while journald and adapter audit details are unavailable through the
    authenticated Telegram interface.
  - **Design:**
    - Persist one bounded SQLite receipt per tool attempt, linked to session, run, action, and audit
      event, plus ordered child events for the OpenCode JSONL stream.
    - Store tool and operation names, canonical resource, timestamps, outcome class, expected and
      observed files, test exit codes, bounded provider report, command digests, verification reason,
      and redacted bounded stdout/stderr tails. Never store environment values, API keys,
      authorization headers, or unrestricted output.
    - Add authenticated `/last-operation` and `/operation <run-id>` Telegram commands plus an
      equivalent CLI inspection command; commands read receipts directly without model inference.
    - Add a read-only `operation_history` adapter so an explicit natural-language request such as
      `show the last OpenCode output` can retrieve the same sanitized receipt. Do not inject logs
      into unrelated model turns.
    - Support `/operation <run-id> log`, `/operation <run-id> diff`, and
      `/operation <run-id> tests`, with Telegram-safe pagination and output bounds.
    - Treat `show the OpenCode operation log` as a receipt request. Keep raw service logs
      administrator-only through journald.
    - Return `no operation found` or an authorization error deterministically rather than asking
      which filesystem log to inspect.
  - **Focused tests:** Successful and failed receipts, parsed JSONL events, restart persistence,
    session isolation, explicit agent retrieval, unrelated-turn non-injection, unauthorized Telegram
    identity, redaction, output bounds, pagination, missing run, and receipt rendering.
  - **Documentation:** Add receipt commands, retention expectations, and the distinction between
    receipts and administrator logs.

- [x] `UT-003` — Add durable active-workspace context instead of treating `current workspace` as a
  literal path.
  - **Priority:** P2
  - **Problem:** File and OpenCode requests fail with `workspace does not exist` even immediately
    after workspace creation because no trusted session field records the active workspace.
  - **Design:**
    - Persist the canonical active workspace per agent session and update it after successful
      workspace creation or an explicitly targeted workspace operation.
    - Inject the active workspace into coordinator requests as trusted runtime context, separate
      from untrusted conversation history.
    - Canonicalize `current workspace` to that stored path before policy evaluation and approval so
      the user sees the real resource being authorized.
    - Add `/workspace`, `/workspaces`, and `/workspace <name>` Telegram commands with equivalent CLI
      operations.
    - Associate every coding operation receipt with its canonical workspace and changed-file list.
      Follow-ups such as `show the file OpenCode created` resolve only when exactly one changed file
      matches; otherwise return a numbered choice rather than guessing.
    - When no workspace is active, offer workspace creation or selection without attempting a file
      or OpenCode action. Never infer a path outside the configured root.
  - **Focused tests:** Create then reuse, explicit selection, process restart, new-session isolation,
    nonexistent selection, path escape, named external repository, and approval resource display.
  - **Documentation:** Document workspace selection, lifecycle, absolute paths, and session scope.

- [ ] `UT-011` — Add a Codex-equivalent personal workspace access profile.
  - **Priority:** P1
  - **Problem:** The current agent requires narrowly registered repositories and treats every
    arbitrary command as risky. This is materially less usable than Codex's normal workspace mode,
    where routine reads, edits, builds, tests, and Git inspection proceed inside one trusted
    workspace and approval is requested only when crossing a boundary.
  - **Design:**
    - Add `hardened` and `personal-workspace` execution profiles. Keep `hardened` as the deployment
      default until the owner explicitly selects the broader profile.
    - Configure canonical `workspace_roots`, optional additional read-only roots, and protected path
      patterns. A selected active workspace must resolve beneath one configured root.
    - In `personal-workspace`, authorize workspace reads automatically and ordinary workspace writes
      through one session grant. Continue requiring per-action approval for network, deletion,
      dependency installation, remote pushes, external messages, credentials, and paths outside the
      active workspace.
    - Add a deterministic command policy with `allow`, `prompt`, and `deny` rules. Routine commands
      such as file inspection, Git status/diff, configured builds, linters, and tests run inside the
      no-network sandbox without repeated approval; unknown or mutating commands prompt.
    - Preserve protected `.git`, `.codex`, `.agents`, `.ssh`, `.gnupg`, cloud credential, browser
      profile, keychain, and secret-file paths unless a narrowly scoped explicit grant is approved.
    - For Linux deployment, support a normal non-root owner account or dedicated-account ACLs,
      `ProtectHome=read-only`, and explicit systemd `ReadWritePaths` for configured workspace roots.
      Never require running the service as root or exposing `/root` wholesale.
    - Prefer rootless Docker or an equivalent non-root sandbox so the service account is not
      effectively root-equivalent through the host Docker socket.
  - **Focused tests:** Routine read/edit/test without repeated approval, session write grant,
    outside-root prompt, network prompt, destructive command prompt, protected path denial, symlink
    escape, profile default, service restart, and Linux filesystem permissions.
  - **Documentation:** Add a profile comparison, migration steps, security warning, and examples for
    Mac local use and a non-root VPS account.

- [ ] `UT-010` — Advertise only tool adapters registered for the current runtime.
  - **Priority:** P2
  - **Problem:** The static coordinator prompt lists OpenCode and other integrations even when their
    adapters are disabled, so the model can propose an action guaranteed to fail as unregistered.
  - **Design:**
    - Build a deterministic capability catalog from registered adapters and enabled operations.
    - Supply only that catalog to the coordinator; keep risk classification and argument schemas in
      code-owned templates.
    - Reject an unavailable-tool proposal before approval and return configuration guidance without
      exposing secret values.
    - Clarify ambiguous phrases such as `workspace log` instead of inventing a filesystem log.
  - **Focused tests:** OpenCode disabled/enabled, Todoist disabled, local execution disabled,
    fallback coordinator consistency, unavailable direct proposal, and ambiguous log wording.
  - **Documentation:** Describe capability-dependent behavior and configuration prerequisites.

- [ ] `UT-012` — Add a typed Codex planner, OpenCode/DeepSeek worker, and Codex reviewer loop.
  - **Priority:** P1
  - **Problem:** The existing Codex coordinator already proposes an OpenCode `code_task`, and
    OpenCode uses DeepSeek to edit the repository, but the handoff is only one generic action. The
    user cannot inspect the full plan before approval, Codex does not review the produced diff and
    tests, and verification failures cannot trigger a bounded correction.
  - **Design:**
    - Introduce a provider-neutral `CodingPlan` schema containing objective, canonical workspace,
      constraints, implementation steps, acceptance criteria, expected files, allowed test commands,
      and risk notes.
    - Use the configured planner route, Codex subscription by default, to produce the plan without
      filesystem mutation. Show the complete bounded plan in the approval card before delegation.
    - Freeze the approved plan with an ID and digest, then pass that exact plan to the OpenCode worker
      configured with DeepSeek. Record worker JSONL events under the operation receipt.
    - Run deterministic before/after manifests, Git evidence, and approved no-network tests outside
      the worker. Never rely on the worker's success claim alone.
    - Give a read-only reviewer route, Codex by default, the approved plan, bounded diff, changed-file
      manifest, test evidence, and sanitized worker report. Require a typed verdict containing met
      criteria, defects, and corrective instructions.
    - Permit one configurable corrective retry without a new approval only when it stays in the same
      approved workspace, uses the same provider network boundary, and requests no new capability.
      Any installation, new network destination, external path, destructive action, or push creates
      a new approval.
    - Render a final provenance summary: planner/model, worker/model, reviewer/model, plan digest,
      changed files, tests, retry count, and receipt/run ID.
    - Preserve provider-neutral fallback independently for planner and reviewer. A fallback may not
      silently change the approved workspace, tools, risk class, or worker provider.
  - **Focused tests:** Codex-plan/OpenCode-implement happy path, approval displays exact plan,
    unexpected file, failed test, reviewer rejection, successful correction, retry exhaustion, new
    capability requiring approval, provider fallback, restart recovery, and provenance rendering.
  - **Documentation:** Add the planner-worker-reviewer sequence, configuration examples, failure
    states, retry rules, and a Telegram walkthrough.

### Integrated acceptance criteria

- After creating `todo-test`, `use the current workspace` resolves to its canonical path across a
  service restart but not across a new session.
- Inside the active workspace, routine reads, edits, Git inspection, builds, and tests behave like
  Codex workspace mode; network and boundary-crossing actions still request approval.
- If OpenCode creates `todo.py` while different files were expected, Telegram names `todo.py`, names
  the missing expected files, states that verification failed, and says the file was retained.
- `show the OpenCode operation log` returns the sanitized receipt for that run without invoking a
  model or exposing provider credentials; an explicit natural-language request can retrieve the same
  receipt through the read-only history adapter.
- `show the file OpenCode created` lists or reads the receipt's changed files from the canonical
  workspace without treating `current workspace` as a literal directory name.
- The approval card shows the Codex-generated coding plan; OpenCode/DeepSeek implements that exact
  plan; deterministic checks run; and Codex returns a review verdict with a bounded correction when
  needed.
- Disabled tools are neither advertised nor sent for approval, and no nonexistent workspace action
  is attempted merely because the user said `current workspace`.

## Completed from testing

- [x] `UT-001` — Clarify hidden token entry, Bot API identity discovery, and empty-update handling.
- [x] `UT-002` — Reject an empty configured Telegram token and add opt-in INFO polling diagnostics.
- [x] `UT-004` — Canonicalize accidental `adapter/operation` tool names before policy evaluation and
  explicitly require separate `tool_name` and `operation` fields in coordinator instructions.
- [x] `UT-005` — Forward stdin with Docker interactive mode, require explicit file content, and verify
  persisted bytes before reporting a local file write as successful.

## Deferred or rejected

Record declined suggestions with a short reason so they are not repeatedly reconsidered without new
evidence.

- `UT-007` — A tmux-managed `personal-agent start` lifecycle wrapper is deferred as unnecessary.
  - `personal-agent chat` already provides the local interactive terminal interface, so a `start`
    alias would add little value.
  - During testing, tmux can directly run the existing `personal-agent telegram` foreground command.
  - For self-deployment, `systemd` should own Telegram startup, restart, status, shutdown, and logs
    instead of adding a temporary tmux process-manager abstraction to the application.
