from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse
import asyncio
import json
import time
import uuid
from typing import AsyncIterator, Optional

app = FastAPI()

# --- Helpers: SSE formatting (RFC/WHATWG event-stream) ---
def sse_encode(data: str, *, event: Optional[str] = None, _id: Optional[str] = None, retry_ms: Optional[int] = None) -> bytes:
    """
    Returns a single SSE "event" frame as bytes, ending with \n\n.
    Lines must not contain bare CR; we use LF and the final blank line as record separator.
    """
    lines = []
    if retry_ms is not None:
        lines.append(f"retry: {retry_ms}")
    if _id is not None:
        lines.append(f"id: {_id}")
    if event is not None:
        lines.append(f"event: {event}")
    # Per spec, split data by lines; each gets "data: "
    for line in data.splitlines() or [""]:
        lines.append(f"data: {line}")
    return ("\n".join(lines) + "\n\n").encode("utf-8")

def sse_comment(comment: str = "") -> bytes:
    # Heartbeat/comment frame; ignored by EventSource but keeps the connection alive
    return (f":{comment}\n\n").encode("utf-8")

# --- Fake LLM token generator (replace with real decode loop) ---
async def generate_tokens(prompt: str, *, start_token_idx: int = 0) -> AsyncIterator[tuple[int, str]]:
    fake = ("This is a fake token stream for " + prompt).split()
    for i in range(start_token_idx, len(fake)):
        await asyncio.sleep(0.05)  # simulate decode latency
        yield i, fake[i]

# --- Stream endpoint ---
@app.get("/v1/stream")
async def stream(request: Request, prompt: str, heartbeat_sec: float = 15.0) -> StreamingResponse:
    """
    SSE stream of tokens.
    - Uses event: "token" for each token, and "done" at the end.
    - Sends periodic ":" comment heartbeats.
    - Honors Last-Event-ID for resume (simple example: treat it as last token index).
    """
    last_event_id = request.headers.get("last-event-id") or request.query_params.get("lastEventId")
    start_idx = 0
    if last_event_id:
        try:
            start_idx = int(last_event_id) + 1
        except ValueError:
            start_idx = 0

    async def event_generator() -> AsyncIterator[bytes]:
        # Initial retry hint (client reconnect backoff if needed)
        yield sse_encode("", retry_ms=15000)

        # Send a ready event with stream metadata
        meta = {"ts": int(time.time()), "prompt_len": len(prompt)}
        yield sse_encode(json.dumps(meta), event="ready", _id=str(start_idx - 1 if start_idx > 0 else 0))

        # Send tokens with IDs = monotonically increasing token indices
        next_heartbeat = time.monotonic() + heartbeat_sec
        async for idx, tok in generate_tokens(prompt, start_token_idx=start_idx):
            # If client disconnected, stop
            if await request.is_disconnected():
                break

            payload = {"token": tok}
            yield sse_encode(json.dumps(payload, ensure_ascii=False),
                             event="token",
                             _id=str(idx))

            # Heartbeat if interval elapsed (keeps proxies/TCP alive)
            now = time.monotonic()
            if now >= next_heartbeat:
                yield sse_comment("keep-alive")
                next_heartbeat = now + heartbeat_sec

        # Final done event (no data required per spec, but we include a small JSON)
        if not await request.is_disconnected():
            done = {"status": "ok", "trace": str(uuid.uuid4())}
            yield sse_encode(json.dumps(done), event="done", _id=str(start_idx + 10_000_000))

    headers = {
        # SSE must be this content type
        "Content-Type": "text/event-stream; charset=utf-8",
        # Prevent caching
        "Cache-Control": "no-cache, no-transform",
        # Required for some proxies/CDNs to not buffer the stream
        "X-Accel-Buffering": "no",
        # Explicit connection hint (some stacks add this automatically)
        "Connection": "keep-alive",
    }
    return StreamingResponse(event_generator(), headers=headers)




<script>
  const es = new EventSource("/v1/stream?prompt=hello");
  es.onopen = () => console.log("SSE open");
  es.onerror = (e) => console.error("SSE error", e);

  es.addEventListener("ready", e => console.log("ready", JSON.parse(e.data)));
  es.addEventListener("token", e => {
    const { token } = JSON.parse(e.data);
    // append token to UI
    console.log("token:", token, "id:", e.lastEventId);
  });
  es.addEventListener("done", e => {
    console.log("done", e.data);
    es.close();
  });
</script>
