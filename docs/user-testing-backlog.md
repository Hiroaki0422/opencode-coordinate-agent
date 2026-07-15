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

- [ ] `UT-003` — A file-write request can target a nonexistent workspace without first guiding the
  user through workspace creation.
  - **Priority:** P2
  - **Environment:** VPS through Telegram
  - **Scenario:** Request creation of `test.txt` before any managed workspace exists
  - **Expected:** Offer or request creation of a named workspace before proposing the file write
  - **Actual:** The write was approved, then failed with `workspace does not exist`
  - **Reproduction:** Confirmed once
  - **Evidence:** Sanitized Telegram error from `local_execution/write_file`
  - **Decision:** Pending broader user-testing triage

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

No accepted change is currently queued for implementation.

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
