# ADR 0003: Use provider-neutral ordered model routing

**Status:** Accepted

## Context

The coordinator must remain useful when one provider is rate-limited, out of credits, unavailable, or no longer preferred. Embedding one vendor's model directly in graph nodes would couple workflow logic to that provider and make fallback behavior difficult to test.

## Decision

Model targets are configured as an ordered list of provider and model names. A provider registry converts each target into a PydanticAI model, and PydanticAI's `FallbackModel` advances to the next target on provider API failures. The LangGraph workflow depends only on the typed coordinator protocol.

OpenAI and DeepSeek builders ship with v1. Additional providers can be registered without changing graph nodes or policy code.

## Consequences

- Provider credentials and model names remain deployment configuration.
- The first healthy model in the ordered route serves the request.
- Structured-output and tool behavior must be evaluated across every configured provider.
- Provider-specific features stay behind builders or tool adapters rather than entering workflow state.
