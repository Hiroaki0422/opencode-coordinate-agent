# ADR 0006: Pin the Codex subscription CLI contract

**Status:** Accepted

## Context

P3.5 will optionally use a ChatGPT Codex subscription through the official Codex CLI rather than
copying OAuth credentials or sending requests through an OpenAI API key. The CLI is an external
process with versioned flags, JSONL events, authentication state, and error text that may change.

## Verified contract

The contract was verified locally against `codex-cli 0.144.0-alpha.4`. This is the minimum supported
version until a later version is explicitly tested and the fixtures are updated.

The future adapter will probe these commands without making a model request:

```text
codex --version
codex exec --help
codex login status
```

The verified non-interactive surface provides:

- `codex exec [PROMPT]` with prompt input either as an argument or stdin;
- `--json` for JSONL events and `--output-schema` for the final response schema;
- `--output-last-message` for a bounded final-response artifact;
- `--sandbox read-only` and `--skip-git-repo-check` for an empty temporary working directory;
- `--ephemeral` to avoid persisted session files;
- `--ignore-user-config` and `--ignore-rules` to avoid user or repository behavior overrides; and
- `--strict-config` to reject unknown configuration fields.

`codex login status` exits `0` and reports `Logged in using ChatGPT` for the verified authenticated
state. With an existing empty `CODEX_HOME`, it exits `1` and reports `Not logged in`. Invalid CLI
arguments exit `2`. The CLI has no execution-timeout flag, so the parent adapter must enforce timeout,
cancellation, output limits, and child-process termination.

## Failure contract

Application code classifies failures into stable sanitized categories: missing executable, missing
login, expired authorization, exhausted subscription, rate limit, malformed JSONL, unsupported
version, timeout, and unclassified process failure. Raw OAuth values and unrestricted stderr must not
enter logs, audit records, checkpoints, or user-facing errors.

The JSONL parser validates framing only. Event-specific fields remain deliberately unpinned because
the final typed response is validated through the supplied JSON Schema and Pydantic model. A future
adapter may accept additional event fields but must reject malformed lines, non-object events, an empty
stream, or a final response that fails schema validation.

## Consequences

- Startup can fail before consuming subscription allowance when the executable, version, flags, or
  login state is unsupported.
- CI tests the contract with fixtures and never invokes a real Codex model.
- Authenticated integration testing remains explicit and opt-in because it consumes subscription usage.
- Upgrading the minimum CLI version requires rechecking help text, login behavior, JSONL framing,
  structured output, exit codes, and read-only isolation.
