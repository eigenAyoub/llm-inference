import aiohttp
import asyncio

from fastapi import FastAPI
from contextlib import asynccontextmanager

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

@asynccontextmanager
async def lifespan(app: FastAPI):
    # smth
    #aiohttp_timeout =  
    app.state.http_client = aiohttp.ClientSession()
    yield
    # smth
    app.state.http_client.close() 
    
app = FastAPI(lifespan=lifespan)


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
        return x 

def sse_heartbeat():
    return ": heartbeat\n\n"



q = asyncio.Queue()

@app.post("/submit_job")
async def submit(prompt: Prompt):
    p = prompt.prompt
    job_id = uuid.uuid4().hex

    async with app.state.http_client.post(
        "http://127.0.0.1:8080/v1/chat/completions",
        json= {"messages":
            [
                {"role":"user","content":p},
            ],
            "stream":True,
            "temperature":0.8,
            }
    ) as resp:
        async for chunk in resp.content:
            y = json.loads(chunk.split(":",1)[1].strip())
            
            await q.put([job_id, chunk])

    return {"received": job_id, "msg": prompt.prompt}
    

@app.get("/events")
async def stream(stream_id: str):

    print(f"we got {stream_id}")

    async def stream_():
        try:
            yield "retry: 2000\n\n"
            yield ": connected\n\n"
            
            while True:
                t = await q.get()
                yield sse_event(t[0], t[1], 1)
                print(t)
                await asyncio.sleep(0.1)
                
                
        except asyncio.CancelledError:
            raise
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


# first just print this shit out
# then format properly back in the app.
# alright



@app.get("/")
async def index():

    response = FileResponse("static/index.html")


    return response
