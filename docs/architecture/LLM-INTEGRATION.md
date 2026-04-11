# Observibot — LLM Integration & Prompt Architecture

## Provider Abstraction

Abstract LLMProvider with implementations:
- **AnthropicProvider** — Claude API (default: claude-sonnet-4-20250514)
- **OpenAIProvider** — OpenAI API (default: gpt-4o)
- **MockProvider** — canned responses for testing
- **OllamaProvider** — local LLM, zero API cost (future)

All providers: parse JSON from response (strip markdown fences), track tokens,
retry 3x with backoff, enforce per-cycle token budget.

## Three Prompt Templates

1. **SYSTEM_ANALYSIS_PROMPT** — interpret SystemModel into business context
2. **ANOMALY_ANALYSIS_PROMPT** — explain anomalies, correlate with changes
3. **ON_DEMAND_QUERY_PROMPT** — answer natural language questions

Each instructs LLM to respond as JSON with a documented schema.

## Onboarding Interview

After discovery, the semantic modeler:
1. Feeds SystemModel to LLM for interpretation
2. Presents findings via rich CLI
3. Asks user to confirm/correct app type, metrics, critical systems
4. Stores business context in SQLite for use in future analysis cycles

## Cost Management

- Context compression (only relevant tables/metrics per analysis)
- Per-cycle token budget cap
- Tiered analysis (LLM only on anomaly or scheduled interval)
- Response caching and insight deduplication
- Cost tracking in llm_usage table

Estimated: $5-15/mo quiet system, $20-40/mo active, $0 with local LLM.
