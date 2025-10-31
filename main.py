import asyncio
from redis.asyncio import Redis
import json, uuid
from pydantic import BaseModel

from fastapi import FastAPI, BackgroundTasks, Request, Response, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from contextlib import asynccontextmanager 

from datetime import datetime
import random
import secrets

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

r = Redis(host="localhost", port=6379, decode_responses=True)
@asynccontextmanager
async def  lifespan(app: FastAPI):
    yield
    await r.close()

app = FastAPI(lifespan=lifespan)

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
        },
        maxlen=5000, 
        approximate=True # this is the default, ~
        )

## set stuff: stream to sessions: to test membership:
async def sadd_stream_to_sid(sid: str, stream_id: str):
    return await r.sadd(f"sid:{sid}:streams", f"{stream_id}")

async def sismember_stream_to_sid(sid: str, stream_id: str):
    return await r.sismember(f"sid:{sid}:streams", f"{stream_id}")

# dealing with streams in redis hashes:
async def hadd_stream(sid, stream_id):
    return await r.hset(
        f"stream:{stream_id}",
        mapping={
            "sid": sid,
            "n_jobs": 0,
            "born": str(datetime.now())
        }
    )

async def create_session():
    sid = secrets.token_urlsafe(32)
    await r.set(f"sid:{sid}", "ok", ex=3600)
    return sid

@app.post("/streams/new")
async def new_stream(request: Request):

    sid = request.cookies.get("sid")

    if not sid:
        raise HTTPException(status_code=401, detail="un-auth")

    if not (await r.exists(f"sid:{sid}")):
        raise HTTPException(status_code=401, detail="you really can't submit a job")

    if (await r.scard(f"sid:{sid}:streams"))>5:
        raise HTTPException(status_code=429, detail="too many streams per sessions")

    await r.expire(f"sid:{sid}", 3600)

    stream_id = uuid.uuid4().hex

    await r.sadd(f"sid:{sid}:streams", stream_id)
    await hadd_stream(sid, stream_id)          
    await r.set(f"stream:{stream_id}:cursor","0-0")  # do you need this?

    print(f">> in /streams/new/ stream_id = {stream_id} for sid = {sid}")

    await r.expire(f"sid:{sid}", 3600)
    await r.expire(f"sid:{sid}:streams", 3600)
    await r.expire(f"stream:{stream_id}", 3600)
    await r.expire(f"stream:{stream_id}:cursor", 3600)

    return {"stream_id": stream_id}


@app.post("/submit_job")
async def submitjob(prompt: Prompt, request: Request, bg_tasks: BackgroundTasks):

    job_id = uuid.uuid4().hex
    sid = request.cookies.get("sid")

    if not sid:
        raise HTTPException(status_code=401, detail="un-auth")

    if not (await r.exists(f"sid:{sid}")):
        raise HTTPException(status_code=401, detail="you really can't submit a job")

    await r.expire(f"sid:{sid}", 3600)

    stream_id = prompt.stream_id
    
    c1 = await r.exists(f"stream:{stream_id}")

    if c1 == 0:
        raise HTTPException(status_code=403, detail="sid is fine, stream does not exist!")

    c2 = await r.hget(f"stream:{stream_id}", "sid")  

    if c2 != sid:
        print(f"c2 = {c2}")
        raise HTTPException(status_code=403, detail="Stream doesn't belong to sid c2")

    is_mem = await r.sismember(f"sid:{sid}:streams", stream_id)

    if not is_mem:
        print(f"sid = {sid} // {stream_id} is not a mem of  {sid} is_mem = {is_mem}")
        raise HTTPException(status_code=403, detail=f"Stream {stream_id} not member of sid:{sid}:streams")

    await r.expire(f"stream:{stream_id}", 3600) # hash
    await r.expire(f"sid:{sid}:streams", 3600)  # set
    await r.hincrby(f"stream:{stream_id}", "n_jobs", 1)
    
    print(f"sid and stream_id are okay, going to schedule the job")

    # this is scheduled after the ack is sent.
    bg_tasks.add_task(generate, prompt.stream_id, job_id)
    return {"received": job_id, "msg": prompt.prompt}

async def generate(stream_id: str, job_id: str):

    reply_id = random.randint(1, 10)
    tokens = llm_things[str(reply_id)].split()

    await asyncio.sleep(0.5)

    for idx, tok  in enumerate(tokens):
        _ = await xadd_h(tok, stream_id, job_id, idx)
        await asyncio.sleep(0.1)

    _ = await xadd_h("eos", stream_id, job_id, len(tokens))
    print(f"Job {job_id} is done at tokens:{stream_id}")

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
        x = f"event: token\ndata: {json.dumps(data)}\nid: {t_id}\n\n"
        print(x)
        return x 

def sse_heartbeat():
    return ": heartbeat\n\n"

async def fan_in(stream_id: str):
    
    cursor = await r.get(f"stream:{stream_id}:cursor")

    next_token = await r.xread({f"tokens:{stream_id}": cursor}, count = 1, block = 3000)

    if next_token != []:
        next_token = next_token[0][1][0]
        rid = next_token[0] 
        tok = next_token[1]["token"]
        job_id = next_token[1]["job_id"]
        await r.set(f"stream:{stream_id}:cursor", rid)
        yield job_id, tok, rid

        if tok == "eos":
            await r.hincrby(f"stream:{stream_id}","n_jobs", -1)
    else:
        yield -1,-1,-1

# you got some SSE syntax here:
@app.get("/events")
async def stream(request: Request, stream_id: str):

    sid = request.cookies.get("sid")
    
    if not sid:
        raise HTTPException(status_code=401, detail="un-auth")

    if not (await r.exists(f"sid:{sid}")):
        raise HTTPException(status_code=401, detail="you really can't submit a job")

    if not (await r.sismember(f"sid:{sid}:streams", stream_id)):
        raise HTTPException(status_code=401, detail="stream id metadata doesn't exist")

    if not (await r.exists(f"stream:{stream_id}")):
        raise HTTPException(status_code=401, detail="stream id meta doesn't exist")
    
    last_event_id = request.headers.get("last-event-id")

    print(f"Stream {stream_id} already exists. last_event_id = {last_event_id}")

    await r.expire(f"sid:{sid}", 3600)
    await r.expire(f"sid:{sid}:streams", 3600)
    await r.expire(f"stream:{stream_id}", 3600)
    await r.expire(f"stream:{stream_id}:cursor", 3600)
    


    async def stream_():
        try:
            if last_event_id:
                print(f"IN STREAM_ cursor moved to last_event_id")
                await r.set(f"stream:{stream_id}:cursor", last_event_id)
            else:
                print(f"IN STREAM_; but no token was ever sent")
                await r.set(f"stream:{stream_id}:cursor", "0-0")

            yield "retry: 2000\n\n"
            yield ": connected\n\n"
            
            while True:
                async for job, tok, t_id in fan_in(stream_id):
                    if tok == -1:
                        print(f"wtf")
                        yield sse_heartbeat()
                    else:
                        yield sse_event(job, tok, t_id)
                        await r.expire(f"sid:{sid}", 3600)
                        await r.expire(f"sid:{sid}:streams", 3600)
                        await r.expire(f"stream:{stream_id}", 3600)
                        await r.expire(f"stream:{stream_id}:cursor", 3600)
                        await r.expire(f"tokens:{stream_id}", 3600)
                        await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            raise
        finally:
            # TODO: clean properly.
            # most redis thingies have expiration tags, so I guess we good?
            
            pass

    return StreamingResponse(
        stream_(),
        media_type="text/event-stream",
        headers={
            "cache-control": "no-cache, no-transform",
            "connection": "keep-alive",
            "x-accel-buffering": "no",
        },
    )

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def index(response: Response, request: Request):

    sid = request.cookies.get("sid")

    if not await r.exists(f"sid:{sid}"):
        sid = await create_session()
        #f"we've just created {sid} at redis key ttl is {await r.ttl(f"sid:{sid}")} ")
    else:
        await r.expire(f"sid:{sid}", 3600)
        #f"session id {sid} is in sessions 
        

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
