# Model providers (OpenAI-compatible endpoints)

The agent talks to any OpenAI-compatible `/chat/completions` endpoint with tool calling.

```bash
export PMIGRATE_PROVIDER=openai
export PMIGRATE_BASE_URL=http://localhost:11434/v1    # local Ollama: no key needed
export PMIGRATE_MODEL=<a model that supports tool calling>
pmigrate check-env                                    # should show "LLM provider configured: OK"
pmigrate run toy-users --variant agent+docs -v --max-total-tokens 1500000
```

Hosted options (OpenRouter, Groq, Gemini's OpenAI-compatible endpoint, ...) work the same way with
`PMIGRATE_BASE_URL` and `PMIGRATE_API_KEY`. Free-tier limits and model lists change: check the provider's current terms.

Practical notes
- Start on `toy-users` (small), then the real tasks. Small local models often cannot drive a 20+ step tool loop;
  that is a legitimate, reportable result, not a bug. Always name the model next to the numbers.
- Free tiers rate-limit: the client retries with backoff, so use `--workers 1`.
- Cost is $0, so compare variants on success, regressions, tokens and time. Set `--max-total-tokens` as the budget.
- Tool-call formatting differs by model; malformed arguments are passed through as empty input and the tool error is
  shown to the model, which usually recovers.
- No model at all? `make serve` (replay mode) and the baselines (`noop`, `bump-pydantic`) already work, and the
  benchmark/harness is the substantive part of the project.
