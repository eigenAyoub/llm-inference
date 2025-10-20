# llm-inference:


## Overview

I'm trying to get into llm serving / llms inferene in general. For now, this is a FastAPI toy service that accepts prompts, generates “LLM” tokens in the background, and streams them to the browser via Server-Sent Events (SSE). 


Below is basically me dumping my code to gpt, and asking it what is this code doing so far, write it as a brief README.md.

## What’s implemented

- **Per-user isolation:** Browser generates `user_id` and sends it to both `POST /submit_job` and `GET /events`; each user has a separate ready queue.
- **Job submission & IDs:** `uuid4().hex` job IDs; `POST /submit_job` returns `{received, msg}`.
- **Token-ready scheduling (no HOL stalls):** Producer enqueues **one ready entry per produced token** (plus one for `EOS`); consumer does not re-enqueue.
- **Per-job token queues:** `toks_per_job[job_id]` buffers `(token, index)`; per-job queue removed on `EOS`.
- **SSE stream:** Emits `event: token` (`{\"job_id\",\"token\"}`) and `event: job_complete` (`{\"job_id\"}`); heartbeats on idle.
- **Demo frontend:** `EventSource` listeners for `token`/`job_complete`; routes tokens via `Map(job_id → span)` to update the UI.

## Flow
1. Client `POST /submit_job?user_id=...` → receives `job_id`.
2. Background generator: for each token → push to `toks_per_job[job_id]` and enqueue `job_id` to `active_jobs[user_id]`.
3. SSE loop `/events?user_id=...`: pop `job_id`, read one token, emit `token` or `job_complete`.

## API
- `POST /submit_job?user_id=...` → `{ \"received\": \"<job_id>\", \"msg\": \"<prompt>\" }`
- `GET /events?user_id=...` → SSE stream (`token`, `job_complete`, heartbeats)
- `GET /` and `/static/*` → demo UI + assets

### Next Steps (always suggested by gpt):

* Harden the SSE protocol — Add id: and retry: fields, handle Last-Event-ID for resume-on-reconnect, and set no-buffer/keep-alive headers to survive proxies.

* Switch to a token-ready scheduler with bounds — Enqueue a job only when a token is available; bound per-job and global queues to enforce backpressure and prevent head-of-line blocking.

* Make jobs durable (Redis) — Persist job status/timestamps and emitted tokens; add idempotency via request_id so duplicate submits return the same job_id.

* Secure multi-tenant usage — Replace ad-hoc user_id with JWT-derived identity, enforce per-tenant rate limits/concurrency caps, and validate input sizes/CORS.

* Add observability & ops hooks — Structured logs with job_id/seq, Prometheus metrics (first-token latency, tokens/sec, queue depths), /healthz & /readyz, and SSE-friendly proxy settings.

### Questions:

* If we hit the time-out here:

```python
    try: 
        job_id = await asyncio.wait_for(active_jobs[user_id].get(), timeout=4.0)
        tok, t_id = await toks_per_job[job_id].get()
        if tok == "EOS":
```

do we lose the "next" job?



### Some stuff I learned:

* **Head of line blocking:**

## References:

### AI usage:

* I use gpt (mostly gpt-5-thinking):
    * To analyse why my design sucks, other alternatives, and how it's done on on real world apps.
    * To give me insights on what to do next, high livel descriptions, and pseudo-code. 
    * Generate server side code (JS).
    * I did not copy paste any python code.

### Some readings, and TODOs:

* versioning in API,. where does the `/v1/chat/..` come from?
  * https://google.aip.dev/185

* 



### Next big steps: GPT recommendations:

