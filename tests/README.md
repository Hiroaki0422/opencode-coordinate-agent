# Testing strategy

- `unit/` covers policy, contracts, state transitions, and tool adapters with no network access.
- `integration/` covers SQLite, LangGraph checkpointing, transport wiring, and local-execution boundaries.
- `fixtures/` holds versioned evaluation requests, mocked tool responses, and adversarial prompt-injection inputs.

Real-provider evaluations are opt-in and must use separate credentials from local development.
