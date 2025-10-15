# llm-inference:

I'm trying to get into sota of llms serving. This is an incremaental project, recent updates are kept on top. Messy [top SSE demo](#sse-demo) is on the buttom.


every new feature is added to the top:

## Steps:

* [0. tiny fastapi/SSE demo](#see-demo)  # i'm still here.
* [References:](#references)
* [AI usage:](#ai-usage)




## 0. tiny SSE demo: 

A minimal single-process FastAPI service that accepts a prompt, generates tokens, and streams them to the client (browser) via **SSE**. 


### What the current code does

- **Submit a job**
  - `POST /submit_job` with `{ "prompt": "..." }`.
  - Returns a numeric `job_id` and echoes the prompt.
  - Schedules a background `generate(job_id, reply_id)` task.

- **Fake token generation**
  - Splits a canned sentence into words and emits one token ~every 200 ms.
  - Pushes `[job_id, token]` into a **single global** `asyncio.Queue`.
  - Emits an `"EOS"` marker at the end.

- **SSE stream**
  - `GET /events` reads from the **global** queue and sends:
    - `event: token` with `data: {"job_id": ..., "token": "..."}`.
    - `event: job_complete` with `data: {"job_id": ...}` when it sees `"EOS"`.

- **Browser client (single EventSource)**
  - Opens one `EventSource('/events')`.
  - Keeps a `Map(job_id → span)` to route tokens to the right `<li>`.
  - Appends tokens as they arrive; marks the job “done” on `job_complete`.

### Limitations + Fixes (gpt's analysis):

- **Global token bus mixes jobs; no isolation or per-job order/backpressure.**  
  **fix:** Per-job bounded `asyncio.Queue` + one SSE stream per job (`/v1/stream?job_id=…`).

- **Fragile SSE (no headers/heartbeats).**  
  **fix:** Production SSE: `Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform`, `Connection: keep-alive`, `X-Accel-Buffering: no`; periodic `: keep-alive`; `retry:` hint.

- **No replay/resume; reconnect loses tokens.**  
  **fix:** Emit monotonic `id:` per job; honor `Last-Event-ID`; optional per-job ring buffer (or map to Redis Stream IDs).

- **No cancellation or cleanup on disconnect; tasks can leak.**  
  **fix:** Job registry `{status, task, outq, seq}`; cancel on client disconnect and via `POST /v1/jobs/{id}/cancel`; purge on terminal events.

- **No backpressure; queues can grow unbounded.**  
  **fix:** Bounded per-job queues (e.g., 256); generator `await`s when writer lags; apply max tokens / max wall-time.

- **No idempotency; duplicate submits create duplicate jobs.**  
  **fix:** Support `Idempotency-Key` on `POST /v1/jobs`; return existing `{job_id, stream_url}` if repeated.

- **Weak error surfacing.**  
  **fix:** Stream structured terminal events: `job_error`, `job_cancelled`, `job_complete` with reasons/metrics.

- **No observability (TTFT/TTFX, tokens/s, queue depth).**  
  **fix:** Capture timestamps (`t_start/t_first/t_last`), compute TTFT/TTFX, tokens/s; log structured JSON per job.

- **API shape not future-proof.**  
  **fix:** Stabilize public API:
  - `POST /v1/jobs` → `{ job_id, stream_url }`
  - `GET /v1/stream?job_id=…` (SSE with `id:`)
  - `POST /v1/jobs/{id}/cancel`  
  Internals can later swap in Redis Streams / JetStream / Kafka without client changes.

- **Optional “edge” swap later**
  - Swap the in-memory per-job queue with **Redis Streams** for cross-process replay:
    - `XADD job:<id>` for tokens, `XREAD` in the SSE handler, `MAXLEN` for retention.
    - Keep the **public API unchanged**.

- **Client JS typos/casing; brittle routing.**  
  **fix:** Correct DOM/Fetch usage; open one `EventSource` per job; idempotent rendering keyed by `id:`.



### References:

### AI usage:

* I use gpt (most likely gpt-5-thinking, at least that's what OpenAI tells me):
    * To give me insights on what to do next.
    * To analyse why my design sucks, other alternatives, and how it's done on on real world apps.
    * Sometimes provide pseudo-code, or high level description of fixes.
    * Generate server side JS code.
    * I did not copy paste any python code.

### Terminology:

* Stateless app:  
* Sticky session:
* Long tail operations:


### TODO:

* [3. Adding redis](#)
* [2. Compatible public API endpoints](#)
* [2. Plugging a real llm (Qwen probably), put gif in](#)
* [1. Improving SSE, per-job queue's with a fan-in, fan-out](#)