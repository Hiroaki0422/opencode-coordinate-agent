# ADR 0001: Run V1 as a single-host deployment

**Status:** Accepted

## Context

The original design split the agent between a VPS control plane and a Mac execution worker. That introduces a private job protocol, host identities, synchronization, network availability handling, and a larger operational surface before V1 has proven its core workflow.

## Decision

V1 runs as one self-contained installation on either a Mac or a VPS. The selected host runs its own transport, LangGraph orchestration, SQLite database, policy service, local tools, OpenCode adapter, and approved workspace.

No host contacts, controls, or synchronizes with another host in V1. The same source code and configuration model support both targets; only the enabled transports and available tools differ by host.

## Consequences

- The local shell and OpenCode execute directly within the current host's policy boundary.
- Each host has independent sessions, approvals, audit records, and local memory.
- Deployment, debugging, and recovery are substantially simpler for V1.
- A future coordination layer must be designed as a new explicit boundary rather than assumed by local tool code.
