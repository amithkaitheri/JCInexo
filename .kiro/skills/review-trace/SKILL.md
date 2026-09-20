---
name: review-trace
description: Pull a Flight Recorder trace by ID and explain what happened step by step
---

# Review Flight Recorder Trace

Fetch a trace from the READY Flight Recorder and explain what happened during that plan generation.

## Instructions

1. Fetch the trace:
   ```bash
   curl -s http://localhost:8001/api/traces/<trace_id> -H "X-API-Key: <key>"
   ```
   
   If no API key is configured (dev mode), the header can be omitted.

2. Display the trace timeline:
   - For each step, show: step name → input → output → duration
   - Highlight any errors or unusual latencies (>3s)
   - Show total token count and total duration

3. Analyze the trace:
   - Was the risk level appropriate for the profile?
   - Were the resources filtered correctly?
   - Did the AI plan match the profile needs?
   - Any anomalies (missing steps, high latency, error steps)?

4. If no specific trace ID given, list recent traces:
   ```bash
   curl -s http://localhost:8001/api/traces?limit=5
   ```

## Trace ID to review

$ARGUMENTS

If no arguments, list the 5 most recent traces and summarize them.

## Expected Steps in a Normal Trace

1. `profile_received` — Profile summary logged
2. `fetch_live_data` — Weather + AQHI fetched
3. `filter_resources` — Resources filtered for profile
4. `gemini_plan_generation` — AI plan generated (tokens + latency)
5. `response_assembled` — Final response built

If any step is missing or shows an error, flag it.
