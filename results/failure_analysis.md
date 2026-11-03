# Failure analysis from recorded tool calls

| Task | Variant | Exp | Result | Steps | Wall s | Calls | read | search | edit | tests | first edit | repeated reads | repeated searches | tests w/o edit | errored calls |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| llm-pydantic-v2 | agent | cmp2 | PASS | 40 | 633 | 61 | 26 | 21 | 8 | 5 | 32 | 0 | 0 | 0 | 2 |
| llm-pydantic-v2 | agent | cmp2 | budget_exhausted | 40 | 356 | 61 | 18 | 34 | 6 | 2 | 30 | 0 | 0 | 1 | 2 |
| llm-pydantic-v2 | agent | cmp2 | budget_exhausted | 40 | 441 | 52 | 21 | 20 | 6 | 3 | 30 | 0 | 0 | 1 | 3 |
| llm-pydantic-v2 | agent | cmp3 | budget_exhausted | 80 | 598 | 101 | 42 | 44 | 13 | 1 | 42 | 1 | 0 | 0 | 10 |
| llm-pydantic-v2 | agent | cmp3 | PASS | 79 | 456 | 83 | 31 | 30 | 10 | 10 | 16 | 0 | 0 | 5 | 2 |
| llm-pydantic-v2 | agent | cmp3 | PASS | 79 | 602 | 92 | 28 | 42 | 14 | 4 | 13 | 0 | 0 | 0 | 2 |
| llm-pydantic-v2 | agent+docs | cmp2 | PASS | 39 | 387 | 55 | 22 | 19 | 8 | 3 | 34 | 0 | 0 | 0 | 2 |
| llm-pydantic-v2 | agent+docs | cmp2 | PASS | 40 | 466 | 63 | 23 | 25 | 8 | 5 | 34 | 0 | 0 | 1 | 1 |
| llm-pydantic-v2 | agent+docs | cmp2 | PASS | 40 | 246 | 40 | 16 | 12 | 8 | 3 | 23 | 0 | 0 | 0 | 0 |
| openjd-model | agent | cmp4 | budget_exhausted | 80 | 340 | 103 | 41 | 32 | 23 | 3 | 52 | 0 | 0 | 2 | 14 |
| openjd-model | agent+docs | cmp5 | budget_exhausted | 80 | 471 | 93 | 38 | 31 | 16 | 2 | 44 | 0 | 2 | 1 | 5 |
| vonage-sdk | agent | cmp6 | budget_exhausted | 76 | 1519 | 104 | 40 | 24 | 25 | 3 | 46 | 3 | 0 | 0 | 14 |
| vonage-sdk | agent+docs | cmp6 | budget_exhausted | 28 | 1503 | 44 | 23 | 12 | 0 | 1 | None | 0 | 0 | 0 | 2 |

Exact-duplicate counts only (same tool and arguments). Near-duplicate searches are not counted, so these are lower bounds.
Results are from recorded traces; they describe behavior, not causes.
