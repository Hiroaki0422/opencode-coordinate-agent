# ADR 0004: Use DuckDuckGo as the first search adapter

**Status:** Accepted

## Context

Web research needs a search index, but v1 should not require another paid API or couple research to the active reasoning-model provider. DuckDuckGo has no supported general search API, while the unofficial `ddgs` package provides best-effort, no-key access suitable for a personal agent.

## Decision

Research depends on a provider-neutral `SearchProvider` protocol and ordered fallback router. V1 registers DuckDuckGo first through `ddgs`. Search, page fetching, text extraction, synthesis, and citation verification remain separate layers.

DuckDuckGo failures are reported explicitly. No response may claim research success without source URLs. Additional providers, including self-hosted SearXNG or model-native search, can be added to the route later.

## Consequences

- V1 web search has no per-query search API fee or required search credential.
- DuckDuckGo access is best-effort and may break or rate-limit because the adapter is unofficial.
- Tests mock the search provider and page network, so CI never depends on live DuckDuckGo availability.
- The provider route can gain paid or self-hosted fallbacks without changing orchestration code.
