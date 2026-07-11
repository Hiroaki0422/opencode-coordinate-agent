# ADR 0005: Run OpenCode as a restricted Docker coding worker

**Status:** Accepted

## Context

OpenCode can read and edit repositories, invoke shell tools, load plugins, contact model providers,
and access paths outside its working directory. Running it directly on the host would bypass the
single-host workspace boundary and duplicate the outer approval system.

## Decision

OpenCode runs non-interactively inside the P2 Docker sandbox. Each task names one exact Git repository
and requires individual risky approval because provider access enables container networking. Inline
OpenCode permissions allow repository reads and edits but deny shell, external directories, web tools,
subagents, plugins, branch changes, installs, destructive commands, commits, and pushes. The parent
adapter runs only allowlisted test commands afterward with networking disabled.

The DeepSeek key is passed through process environment inheritance and never command arguments. A task
is successful only when Git evidence verifies the requested files changed and requested tests pass.

## Consequences

- Coding tasks cannot install missing dependencies or change branches automatically.
- A user handles exceptional installs or branch operations as separate risky actions.
- Existing repositories must be explicitly allowlisted; new repositories stay under the workspace root.
- OpenCode upgrades require rebuilding the pinned sandbox image and rerunning coding evaluations.
