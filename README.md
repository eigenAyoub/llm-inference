# llm-inference:


I'm slowly building a tiny local inference engine based on some local gguf llm.  

### AI usage:

I use gpt5 mostly to find flaws in my code, suggest the next steps, and sometimes generate JS/client code. But **never** to write code for the server side. That’s why there are a lot of print statements. This `README` is also me dumping my code to gpt, and asking it to generate a very short readme... obviously it's not that short.

# Minimal Realtime LLM Streaming Server (WIP)

This is a FastAPI + Redis + SSE serving layer around a local LLM (llama.cpp or any OpenAI-style streaming backend). So far It provides:

- Per-session auth via HTTP-only cookie (`sid`) with sliding TTL in Redis.
- Per-session "streams" so each browser tab can open its own live EventSource.
- token-by-token streaming over SSE semantics, with heartbeats and auto-retry.
- Redis-backed replay buffer using Redis Streams (`XADD` / `XREAD`), and `Last-Event-ID`.
- Background generation tasks that forward tokens from the model into Redis in real time.

This is still WIP. Missing pieces (with detail below): 

* scheduler/fairness, 
* cancellation of abandoned work, 
* per-job registry, 
* robust multi-consumer replay.
* Proper FAISS integration from [the other repo](https://github.com/eigenAyoub/ML-prod/blob/main/main.py).



---

## How it works (current state)

1. **Session (`sid`)**
   - Hitting `/` creates a session id (`sid`), stores it in Redis with TTL, and sets it as an `HttpOnly` cookie.
   - TTL is extended on activity.

2. **Stream (`stream_id`)**
   - Browser POSTs `/streams/new`.
   - Server creates a `stream_id`, ties it to that `sid`, and allocates Redis keys:
     - `tokens:{stream_id}` (Redis Stream for generated tokens)
     - `stream:{stream_id}` (hash with `sid`, `n_jobs`, timestamps)
     - `stream:{stream_id}:cursor` (last delivered entry id for replay)

3. **SSE channel**
   - Browser opens `EventSource` to `/events?stream_id=...`.
   - Server checks that the cookie `sid` owns that `stream_id`.
   - Server starts sending:
     - `retry:` (auto-reconnect hint)
     - `: heartbeat` frames
     - `event: token` frames with `{job_id, token}`
     - `event: job_complete` with `{job_id}` at EOS
   - Each SSE frame includes an `id:` equal to the Redis Stream entry id. On reconnect the browser sends `Last-Event-ID`, and the server resumes from there (single-consumer case).

4. **Job submit**
   - Browser POSTs `/submit_job` with `{prompt, stream_id}`.
   - Server validates ownership and schedules a background `generate()` task.

5. **Token generation**
   - `generate()` calls the local model server (OpenAI-compatible streaming endpoint on `localhost:8080/v1/chat/completions` with `"stream": true`).
   - As chunks arrive, tokens are extracted and appended to `tokens:{stream_id}` via `XADD`.
   - On EOS, a final marker is appended so the browser can mark that job as complete.

---

## Missing: 

- No scheduler / fairness yet:
  - Any client can spam `/submit_job`, and each job immediately opens its own streaming request to the model backend.
  - There is no per-session concurrency limit or first-token fairness.

- No per-job registry in Redis:
  - We don't persist job status (`queued`, `running`, `done`, `cancelled`, `error`).
  - We can't cancel abandoned work yet.

- Cursor replay is global per `stream_id`:
  - Multiple tabs watching the same `stream_id` would fight over the same cursor.
  - For now we assume one tab → one `stream_id`.

- TTL/cleanup:
  - Idle tabs for a long time may let the Redis session TTL expire even though the UI is still open.
  - Disconnected clients do not currently cancel in-flight generations.

---

## Run it locally

### 1. Start Redis / llama-server

```bash
redis-server

./llama-server   -m ~/.cache/llama.cpp/ggml-org_gemma-3-1b-it-GGUF_gemma-3-1b-it-Q4_K_M.gguf   --port 8080
```

### 2. Run the FastAPI app


```bash
fastapi run main.py
```

### 3. Open the UI

Open:

```text
http://127.0.0.1:8000/
```

In the browser:

- It ensures there is a `stream_id` (via `/streams/new` if needed).
- It opens an `EventSource` to `/events?stream_id=...`.
- When you submit the form, it POSTs `/submit_job`, creates a `<li>` for that job, and live-appends tokens into that row as they arrive over SSE.



## Learned:


#### -np Vs -cb in `llama-server` 

| `-np, --parallel N` | number of parallel sequences to decode (default: 1)<br/>(env: LLAMA_ARG_N_PARALLEL) |
| `-cb, --cont-batching` | enable continuous batching (a.k.a dynamic batching) (default: enabled)<br/>(env: LLAMA_ARG_CONT_BATCHING) |


###  copy these prompts:

what's a tajine? in detail
what's a croissant? in detail
what is ptx?  in detail