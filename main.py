import asyncio
from redis.asyncio import Redis
import json, uuid
from pydantic import BaseModel
import aiohttp

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

r = Redis(host="localhost", port=6379, decode_responses=True)

@asynccontextmanager
async def  lifespan(app: FastAPI):
    app.state.http_client = aiohttp.ClientSession()
    yield
    await r.close()
    app.state.http_client.close()

app = FastAPI(lifespan=lifespan)

async def xadd_h(token: str, stream_id: str, job_id: str):
    if token == "eos":
        token_type = "eos"
    else:
        token_type = "token"

    return await r.xadd(    
        f"tokens:{stream_id}",
        {
            "token":  token,
            "type":   token_type, 
            "job_id": job_id,
            "stream": stream_id
        },
        maxlen=5000, 
        approximate=True # this is the default, ~
        )

## set stuff: stream to sessions: to test membership:
async def sadd_stream_to_sid(sid: str, stream_id: str):
    return await r.sadd(f"sid:{sid}:streams", stream_id)

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
    
    job_id = uuid.uuid4().hex
    
    await r.lpush(f"{sid}:{stream_id}:waiting_jobs", job_id)   # jobs associated with the stream.
    
    await r.hset(f"{sid}:{stream_id}:{job_id}",
                 mapping={
                     "prompt": prompt.prompt,
                     "status": "fresh", # "fresh" | "processing" | "done"
                     "sid": sid,
                     "stream_id": stream_id
                 })

    await r.expire(f"{sid}:{stream_id}:waiting_jobs", 3600) 
    await r.expire(f"{sid}:{stream_id}:{job_id}", 3600)

    print(f"sid and stream_id are okay, going to schedule the job = {job_id}")
    
    #bg_tasks.add_task(generate, sid, prompt.stream_id, job_id, prompt.prompt)
    # or i can just use asyncio.create_task() here?
    # why did you choose this
    return {"received": job_id, "msg": prompt.prompt}

async def generate(sid: str, stream_id: str, job_id: str):

    # to guarentee that a spot has been created for the job in the UI, fix this.
    first_token = True 
    p =  await r.hget(f"{sid}:{stream_id}:{job_id}", "prompt")
    print(f"we are about to fire the {p} of {sid, stream_id, job_id}")
    
    
    async with app.state.http_client.post(
        "http://127.0.0.1:8080/v1/chat/completions",
        json= {"messages":
            [
                {"role":"user","content": f"Answer with a short sentence: {p}"},
            ],
            "stream":True,
            "temperature":0.8,
            }
    ) as resp:
        async for chunk in resp.content:

            if first_token:
                await r.hset(f"{sid}:{stream_id}:{job_id}", "status", "processing")
                first_token = False
                
            sse_frame = chunk.decode().split(":",1)
            if len(sse_frame) == 1:
                # can ignore 
                continue
            assert sse_frame[0] == "data", "if it's not even data, do you even know what you are doing?"
            sse_json = sse_frame[1]  # json data of the sse frame. // but could be just [DONE]\n
            if sse_json.endswith("[DONE]\n"):
                print(f"Active jobs before {await r.scard(f"{sid}:{stream_id}:active_jobs")}")
                await r.hset(f"{sid}:{stream_id}:{job_id}", "status", "done")
                await r.srem(f"{sid}:{stream_id}:active_jobs", job_id)
                print(f"Active jobs after {await r.scard(f"{sid}:{stream_id}:active_jobs")}")
            else:
                y = json.loads(sse_json.strip())
                finish_reason = y["choices"][0]["finish_reason"]
                #print(y["choices"][0]["delta"], finish_reason)
                if finish_reason == None:
                    token = y["choices"][0]["delta"].get("content")
                    if token:
                        _ = await xadd_h(str(token), stream_id, job_id)
                    else:
                        print(f"No tokens! {y, token}")
                elif finish_reason == "stop":
                    # model layer termination signal
                    print(f"End of model output {y["choices"][0]["delta"]} ")
                    _ = await xadd_h("eos", stream_id, job_id)
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
            "token": token,
        }
        x = f"event: token\ndata: {json.dumps(data)}\nid: {t_id}\n\n"
        return x 

def sse_heartbeat():
    return ": heartbeat\n\n"

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
            
            # this is not the intended behavior of last_event_id!
            if last_event_id:
                print(f"IN STREAM_ cursor moved to last_event_id")
                await r.set(f"stream:{stream_id}:cursor", last_event_id)
            else:
                print(f"IN STREAM_; but no token was ever sent")
                await r.set(f"stream:{stream_id}:cursor", "0-0")

            yield "retry: 2000\n\n"
            yield ": connected\n\n"
            
            cursor = await r.get(f"stream:{stream_id}:cursor")
            while True:
                while await r.scard(f"{sid}:{stream_id}:active_jobs") < 4:
                    if await r.llen(f"{sid}:{stream_id}:waiting_jobs") > 0:
                        new_job = await r.rpop(f"waiting_jobs") 
                        await r.sadd(f"{sid}:{stream_id}:active_jobs", new_job) 
                    else:
                        break
                    
                next_token = await r.xread({f"tokens:{stream_id}": cursor}, count = 1, block = 3000)
                if next_token != []:
                    next_token = next_token[0][1][0]
                    rid = next_token[0] 
                    tok = next_token[1]["token"]
                    job_id = next_token[1]["job_id"]
                    await r.set(f"stream:{stream_id}:cursor", rid)
                    yield sse_event(job_id, tok, rid)
                    await r.expire(f"sid:{sid}", 3600)
                    await r.expire(f"sid:{sid}:streams", 3600)
                    await r.expire(f"stream:{stream_id}", 3600)
                    await r.expire(f"stream:{stream_id}:cursor", 3600)
                    await r.expire(f"tokens:{stream_id}", 3600)
                    if tok == "eos":
                        await r.hincrby(f"stream:{stream_id}","n_jobs", -1)
                else:
                    yield sse_heartbeat()
                
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
