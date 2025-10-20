import asyncio  
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

Q_SIZE = 32

active_jobs: dict[str, asyncio.Queue] = {}
toks_per_job: dict[str, asyncio.Queue] = {}

id_per_stream = defaultdict(int)
job_counter = defaultdict(int)

STREAMS = defaultdict(dict)          # (sid, stream_id): {.. time ..}
SID_TO_STREAMS = defaultdict(set)    #  sid: {.. stream_id1, steam_id2,.. } 

SESSIONS = dict()    # sid : {username / ttl}
SESSION_TTL = timedelta(hours=1) 

def create_session(username: str):
    sid = secrets.token_urlsafe(32) 
    session_id = {
        "username" : username,
        "ttl": datetime.now() + SESSION_TTL
    }
    SESSIONS[sid] = session_id 
    return sid

@app.post("/submit_job")
async def submitJob(prompt: Prompt, request: Request, bg_tasks: BackgroundTasks):
    #print(f"Cookies from submit:\n{request.cookies}")
    job_id = uuid.uuid4().hex
    sid = request.cookies.get("sid")
    print(f"Submit {job_id}")
    
    if not sid:
        raise HTTPException(status_code=401, detail="un-auth")
    rec = SESSIONS.get(sid)
    if not rec or datetime.now() >= rec["ttl"]:
        raise HTTPException(status_code=401, detail="you really can't submit a job")
    rec["ttl"] = datetime.now() + SESSION_TTL

    stream_id = prompt.stream_id
    if (sid,stream_id) not in STREAMS:
        raise HTTPException(status_code=403, detail="sid is fine, but can't recognize stream_id")

    bg_tasks.add_task(generate, prompt.stream_id, job_id, ) # this is scheduled after the ack is sent.
    return {"received": job_id, "msg":prompt.prompt}

async def generate(stream_id: str, job_id: str):

    ready_q = active_jobs.get(stream_id)

    if ready_q is None:
        return

    if job_id in toks_per_job:
        # maybe we started it before!
        return

    toks_per_job[job_id] = asyncio.Queue(maxsize=Q_SIZE)
    job_counter[job_id] = 0

    reply_id = randint(1, 11)
    tokens=  llm_things[str(reply_id)].split()
    await asyncio.sleep(0.5)

    for idx, tok in enumerate(tokens):
        if active_jobs.get(stream_id) is None:
            toks_per_job.pop(job_id, None)
            job_counter.pop(job_id, None)
            return

        await toks_per_job[job_id].put((tok, idx))
        if job_counter[job_id] == 0:
            await ready_q.put(job_id)
        job_counter[job_id] += 1
        await asyncio.sleep(0.2)

    if active_jobs.get(stream_id) is None:
        toks_per_job.pop(job_id, None)
        job_counter.pop(job_id, None)
        return

    await toks_per_job[job_id].put(("EOS", len(tokens)))
    if job_counter[job_id] == 0:
        await ready_q.put(job_id)
    job_counter[job_id] += 1

def sse_event(job_id, token, t_id):
    if token == "EOS":
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
        tok, _ = await toks_per_job[job_id].get()
        job_counter[job_id] -= 1
        t_id   = id_per_stream[stream_id]
        id_per_stream[stream_id] += 1
        yield job_id, tok, t_id
        if tok == "EOS":
            toks_per_job.pop(job_id) # remove it from
            job_counter.pop(job_id)
        elif job_counter[job_id] > 0:
            active_jobs[stream_id].put_nowait(job_id)
    except asyncio.TimeoutError:
        yield -1,"dead",id_per_stream[stream_id]
        pass


## you got some shit SSE syntax here:
@app.get("/events")
def stream(request: Request):

    #print(f"Cookies from events: {request.cookies}")
    #print(f"Headers of this request:")
    #for k,v in dict(request.headers).items():
    #    print(k,v)

    sid = request.cookies.get("sid")
    if not sid:
        raise HTTPException(status_code=401, detail="un-auth")
    rec = SESSIONS.get(sid)
    if not rec or datetime.now() >= rec["ttl"]:
        raise HTTPException(status_code=401, detail="you really can't submit a job")

    rec["ttl"] = datetime.now() + SESSION_TTL
    if len(SID_TO_STREAMS[sid]) > 5:
        raise HTTPException(status_code=429, detail="you really can't submit a job")

    stream_id =  uuid.uuid4().hex
    STREAMS[(sid, stream_id)]["time"] = datetime.now()
    SID_TO_STREAMS[sid].add(stream_id)
    active_jobs[stream_id] = asyncio.Queue(maxsize=Q_SIZE)

    print(f"In events:")
    print(f"Sessions")

    for u,v in SESSIONS.items():
        print(u, v)

    print(f"SID to sessions:")
    for u,v in SID_TO_STREAMS.items():
        print(u, v)

    async def stream_():
        try:
            data = {
                "stream_id": stream_id,
                }

            yield f"event: stream_id\ndata: {json.dumps(data)}\n\n"
            yield f"retry: 2000\n\n"
            yield f": connected\n\n"
            while True:
                async for job, tok, t_id in fan_in(stream_id):
                    if tok == "dead":
                        yield sse_heartbeat()
                    else:
                        yield sse_event(job, tok, t_id)
        except asyncio.CancelledError:
            raise
        finally:
            STREAMS.pop((sid, stream_id), None)
            s = SID_TO_STREAMS.get(sid)
            if s: s.discard(stream_id)
            id_per_stream.pop(stream_id, None)

            q = active_jobs.get(stream_id)
            if q:
                try:
                    while True:
                        q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                active_jobs.pop(stream_id, None)

    return StreamingResponse(
        stream_(), 
        media_type="text/event-stream",
        headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",  
        } 
    )

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index(response: Response, request: Request):

    print(f"Calling SID_TO_STREAMS from get")
    for k,v in SID_TO_STREAMS.items():
        print(k,v)
    
    sid = request.cookies.get("sid")
    
    if sid not in SESSIONS:
        print(f"session id {sid} is not in SESSIONS")
        sid = create_session("Ayoub")
        print(f"We just created {sid}")
    else:
        print(f"session id {sid} is in SESSIONS, new stream will be created in events")
        

    response = FileResponse("static/index.html")

    response.set_cookie(key="sid", 
                        value=sid,
                        path="/", 
                        secure=False, 
                        httponly=True,
                        samesite="lax",
                        max_age=3600
                        ) 
    return response 

