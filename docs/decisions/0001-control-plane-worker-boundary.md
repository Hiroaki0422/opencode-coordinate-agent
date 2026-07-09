# ADR 0001: Separate the VPS control plane from the Mac execution worker

**Status:** Accepted

## Context

The agent must be available from Telegram and SSH even when the Mac is asleep, while some capabilities—local repositories, shell commands, and OpenCode—must run on the Mac. Exposing those capabilities through a public endpoint would create an unnecessarily large attack surface.

## Decision

Run the control plane on the VPS. It owns identity, session state, approvals, LangGraph orchestration, SQLite persistence, and public Telegram webhook handling. Run a separate Mac worker that accepts only authenticated, expiring, scoped jobs. The worker establishes an outbound connection to the VPS or polls for work; it has no public inbound listener.

Use Tailscale for private administrative access and, when appropriate, to protect internal traffic. Require the Mac worker to validate the same job scope and expiry that the control plane validated.

## Consequences

- Telegram and CLI workflows remain available when the Mac worker is offline; local jobs wait or report that the worker is unavailable.
- The Mac can be granted narrowly scoped capabilities without putting its filesystem or shell directly on the internet.
- The system needs a worker-job protocol, reconnection logic, and explicit handling for unavailable or stale workers.
- SQLite remains simple for a single control plane; its data is backed up from the VPS.
