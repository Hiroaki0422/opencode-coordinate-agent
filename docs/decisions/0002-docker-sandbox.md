# ADR 0002: Require Docker for V1 local execution

**Status:** Accepted

## Context

The agent will execute model-proposed shell and coding actions on either a Mac or VPS. Path checks and command allowlists reduce accidental misuse but do not isolate a process from host credentials, sockets, or other files available to the current operating-system user.

## Decision

V1 requires Docker for shell and OpenCode execution. The application invokes commands inside a short-lived container with only the explicitly approved workspace mounted. Network access, environment variables, resource limits, and container capabilities are denied by default and enabled only through explicit policy.

There is no automatic native-execution fallback. If Docker is missing or unhealthy, local execution fails closed and the agent explains that the sandbox is unavailable.

## Consequences

- Mac and VPS deployments need a working Docker installation before enabling local execution.
- Local actions use the same isolation contract on both deployment targets.
- Workspace mounts and Docker daemon access remain sensitive and must be narrowly configured.
- Container images, allowed mounts, network policy, and resource limits become versioned application configuration.
