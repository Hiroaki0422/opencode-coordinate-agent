# Testing strategy

- `unit/` covers policy, contracts, state transitions, and tool adapters with no network access.
- `integration/` covers SQLite, LangGraph checkpointing, transport wiring, and local-execution boundaries.
- `fixtures/` holds versioned evaluation requests, mocked tool responses, and adversarial prompt-injection inputs.

The P0/P1 evaluation catalog covers ordinary reads, expired grants, resource-scope mismatch, duplicate checkpoint resume, prompt injection, and provider failures.

Real-provider evaluations are opt-in and must use separate credentials from local development.
