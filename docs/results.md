# Results: simple versions against their replacements

Each optimized piece replaced a simple version behind the same interface, and a setting
switches back for comparison (`TOKEN_BUDGET`, `PROMPT_ESTIMATE`, `FALLBACK_STRATEGY`). The
numbers below are what the tests printed in one run; millisecond timings vary a little from
run to run.

## Reproduce

With Redis running, and Ollama running with `llama3.2:3b` for the last test:

```bash
pytest -s -p no:cacheprovider \
  "tests/test_token_budgets.py::test_a_burst_across_a_boundary_gets_2x_from_a_fixed_window_but_about_1x_from_a_bucket" \
  "tests/test_prompt_estimate.py::test_a_long_conversation_does_not_inflate_a_later_one_message_request" \
  "tests/test_gateway.py::test_with_ollama_hung_an_open_breaker_stops_auto_waiting_for_the_timeout" \
  "tests/test_ollama_integration.py::test_the_calibrated_estimate_lands_close_to_real_usage"
```

## Token budget: fixed window → token bucket

A burst just before a window boundary and again just after it, limit 100, in requests of 10
tokens. The test uses one-second windows and a one-second refill so it never waits for a
real minute; the algorithms are the same at any scale.

```
fixed window: 100 + 100 = 200 tokens admitted within 132 ms across a boundary, 2.0x the limit of 100
token bucket: 100 + 10 = 110 tokens admitted within 123 ms across a boundary, 1.1x the limit of 100
```

The bucket's extra 10 tokens are what it refilled during the burst.

## Prompt estimate: characters ÷ 4 → calibrated

Against a real `llama3.2:3b`, after the calibrated estimate learned from one different
prompt (`hi`):

```
prompt tokens for 'Name one planet. One word.': real 32; characters / 4 estimated 7 (4.6x too low); calibrated estimated 39 before any real count and 32 after learning from one different prompt
```

With the per-message term, a long conversation does not inflate later short requests:

```
one-message request after a 20-message chat (real: 26 tokens): reserves 121 without the per-message term, 26 with it
```

## Fallback: sequential → circuit breaker

Four `auto` requests with Ollama hung, at a 0.3-second read timeout:

```
sequential fallback: auto request latencies with Ollama hung (0.3s read timeout): 315 ms, 313 ms, 312 ms, 311 ms; Ollama was tried 4 times
circuit breaker: auto request latencies with Ollama hung (0.3s read timeout): 312 ms, 311 ms, 313 ms, 11 ms; Ollama was tried 3 times
```

After three failures the breaker opens, and the fourth request skips Ollama instead of
waiting for the timeout.
