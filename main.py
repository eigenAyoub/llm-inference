import asyncio
from redis.asyncio import Redis
import json, uuid
from pydantic import BaseModel
from collections import defaultdict

from fastapi import FastAPI, BackgroundTasks, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

import time
from datetime import datetime, timedelta

from numpy.random import randint
import secrets

app = FastAPI()

class Prompt(BaseModel):
    prompt: str
    stream_id: str

llm_things = {
    "1":  "Tokyo is the capital of Japan and is known for its blend of traditional culture and cutting-edge technology.",
    "2":  "Paris, the capital of France, is famous for its art, fashion, and the iconic Eiffel Tower.",
    "3":  "Ottawa is the capital of Canada and is home to Parliament Hill and the Rideau Canal.",
    "4":  "Canberra is the capital of Australia, located between Sydney and Melbourne, designed as a planned city.",
    "5":  "Nairobi is the capital of Kenya and serves as a major hub for African business and wildlife tourism.",
    "6":  "Brasília, the capital of Brazil, was built in the 1960s and is renowned for its modernist architecture.",
    "7":  "Berlin is the capital of Germany and a city rich in history, art, and vibrant nightlife.",
    "8":  "New Delhi is the capital of India and houses important government buildings and historic landmarks.",
    "9":  "London, the capital of the United Kingdom, is a global center for finance, culture, and education.",
    "10": "Rome is the capital of Italy and is famous for its ancient history, architecture, and the Vatican City."
}

q_size = 32
date_format = "%Y-%m-%d %H:%M:%S.%f"

active_jobs: dict[str, asyncio.Queue] = {}
toks_per_job: dict[str, asyncio.Queue] = {}

session_ttl = timedelta(hours=1)

## Redis mirrors:

r = Redis(host="localhost", port=6379, decode_responses=True)

async def xadd_h(token: str, stream_id: str, job_id: str,  idx: int):
    if token == "eos":
        token_type = "eos"
    else:
        token_type = "token"

    return await r.xadd(
        f"tokens:{stream_id}",
        {
            "token":  token,
            "type":   token_type, 
            "idx": idx,
            "job_id": job_id,
            "stream": stream_id
        })


# should probably be a hash? or set?

async def xadd_session(sid: str):
    # add a session to the list of sessions
    return await r.xadd(
        f"sessions",
        {
            "sid":  sid,
            "ttl": str(datetime.now() + session_ttl)
        })


# sid_to_redis_id = dict()    # sid > redis_id

## set stuff: stream to sessions: to test membership:
async def sadd_stream_to_sid(sid: str, stream_id: str):
    return await r.sadd(f"sid:{sid}:streams", f"{stream_id}")

async def sismember_stream_to_sid(sid: str, stream_id: str):
    return await r.sismember(f"sid:{sid}:streams", f"{stream_id}")

async def sremvove_stream_from_sid(sid: str, stream_id: str):
    return await r.srem(f"sid:{sid}:streams", f"{stream_id}")

# dealing with sessions:

async def create_session():
    sid = secrets.token_urlsafe(32)
    #sid_to_redis_id[sid] = await xadd_session(sid)
    await r.set(f"sid:{sid}", "ok", ex=3600)
    print(f"From create_session:  We have just created a session {sid}")
    print(f"Does the redis string sid:{sid} exists > {await r.get(f"sid:{sid}")}")
    return sid

@app.post("/submit_job")
async def submitjob(prompt: Prompt, request: Request, bg_tasks: BackgroundTasks):
    # print(f"cookies from submit:\n{request.cookies}")
    job_id = uuid.uuid4().hex
    sid = request.cookies.get("sid")

    if not sid:
        raise HTTPException(status_code=401, detail="un-auth")
    
    if (await r.ttl(f"{sid}:sid")) > 0:
        raise HTTPException(status_code=401, detail="you really can't submit a job")

    r.expire(f"sid:{sid}", 3600)

    stream_id = prompt.stream_id

    if not (await r.sismember(f"sid:{sid}:streams",f"{stream_id}")) :
        print(f"stream_id {stream_id} is not part of the streams registered for the session {sid}")
        raise HTTPException(status_code=403, detail="sid is fine, but can't recognize stream_id")

    # this is scheduled after the ack is sent.
    bg_tasks.add_task(generate, prompt.stream_id, job_id)
    return {"received": job_id, "msg": prompt.prompt}


async def generate(stream_id: str, job_id: str):

    ready_q = active_jobs.get(stream_id)

    if ready_q is None:
        return

    if job_id in toks_per_job:
        # maybe we started it before!
        return

    toks_per_job[job_id] = asyncio.Queue(maxsize=q_size)

    reply_id = randint(1, 11)
    tokens = llm_things[str(reply_id)].split()
    await asyncio.sleep(0.5)

    for idx, tok  in enumerate(tokens):
        if active_jobs.get(stream_id) is None:
            toks_per_job.pop(job_id, None)
            return

        # redis mirror 
        id_tok = await xadd_h(tok, stream_id, job_id, idx)

        await toks_per_job[job_id].put((tok, id_tok))

        if idx == 0:                      # first token => advertise job once
            await ready_q.put(job_id)
         
        await asyncio.sleep(0.2)

    if active_jobs.get(stream_id) is None:
        toks_per_job.pop(job_id, None)
        return

    # redis mirror 
    id_tok = await xadd_h("eos", stream_id, job_id, len(tokens))
    await toks_per_job[job_id].put(("eos", id_tok))

def sse_event(job_id, token, t_id):
    if token == "eos":
        data = {
            "job_id": job_id,
        }
        return f"event: job_complete\ndata: {json.dumps(data)}\nid: {t_id}\n\n"
    else:
        data = {
            "job_id": job_id,
            "token": f"{token}_{t_id}",
        }
        return f"event: token\ndata: {json.dumps(data)}\nid: {t_id}\n\n"

def sse_heartbeat():
    return ": heartbeat\n\n"


async def fan_in(stream_id: str):
    try:
        job_id = await asyncio.wait_for(active_jobs[stream_id].get(), timeout=4.0)
        tok, rid = await toks_per_job[job_id].get()
        # redis mirror

        yield job_id, tok, rid
        if tok == "eos":
            toks_per_job.pop(job_id)  # remove it
        else:
            active_jobs[stream_id].put_nowait(job_id)   # <— unconditional requeue

    except asyncio.TimeoutError:
        yield -1, "dead", "-1"
        pass


# you got some SSE syntax here:
@app.get("/events")
async def stream(request: Request):

    sid = request.cookies.get("sid")
    
    if not sid:
        raise HTTPException(status_code=401, detail="un-auth")

    # print(f"inside events/: {await r.get(f"sid:{sid}")} {await r.ttl(f"sid:{sid}")}")
    if (await r.ttl(f"{sid}:sid")) > 0:
        raise HTTPException(status_code=401, detail="you really can't submit a job")


    if (await r.scard(f"sid:{sid}:streams"))>5:
        raise HTTPException(status_code=429, detail="too many streams per sessions")

    stream_id = uuid.uuid4().hex
    active_jobs[stream_id] = asyncio.Queue(maxsize=q_size)
    await sadd_stream_to_sid(sid, stream_id)

    async def stream_():
        try:
            data = {
                "stream_id": stream_id,
            }

            yield f"event: stream_id\ndata: {json.dumps(data)}\n\n"
            yield "retry: 2000\n\n"
            yield ": connected\n\n"
            while True:
                async for job, tok, t_id in fan_in(stream_id):
                    if tok == "dead":
                        yield sse_heartbeat()
                    else:
                        yield sse_event(job, tok, t_id)
        except asyncio.CancelledError:
            raise
        finally:
            pass
            await sremvove_stream_from_sid(sid, stream_id)
            s = await r.get(f"sid:{sid}")
            print(f"you have to fix this shit.")
            if s:
                s.discard(stream_id)

            q = active_jobs.get(stream_id)
            if q:
                try:
                   while True:
                        q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                active_jobs.pop(stream_id, None)

            # TODO: redis clean-up

    return StreamingResponse(
        stream_(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache",
            "connection": "keep-alive",
            "x-accel-buffering": "no",
        },
    )

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index(response: Response, request: Request):

    sid = request.cookies.get("sid")

    if not await r.get(f"{sid}:sid"):
        sid = await create_session()
        print(f"we've just created {sid} at redis key ttl is {await r.ttl(f"sid:{sid}")} ")
    else:
        print("session id is in sessions, new stream will be created in events")

    response = FileResponse("static/index.html")

    response.set_cookie(
        key="sid",
        value=sid,
        path="/",
        secure=False,
        httponly=True,
        samesite="lax",
        max_age=3600,
    )
    return response
