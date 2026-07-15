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

## Accepted changes

Move an incoming item here only after its behavior and scope are understood. Every accepted change
must identify focused tests and documentation updates before implementation begins.

## Completed from testing

- [x] `UT-001` — Clarify hidden token entry, Bot API identity discovery, and empty-update handling.
- [x] `UT-002` — Reject an empty configured Telegram token and add opt-in INFO polling diagnostics.
- [x] `UT-004` — Canonicalize accidental `adapter/operation` tool names before policy evaluation and
  explicitly require separate `tool_name` and `operation` fields in coordinator instructions.

## Deferred or rejected

Record declined suggestions with a short reason so they are not repeatedly reconsidered without new
evidence.
