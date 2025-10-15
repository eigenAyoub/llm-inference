import asyncio  
import json, uuid, random 
from pydantic import BaseModel
from collections import defaultdict

from fastapi import FastAPI, BackgroundTasks 
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles


from collections import defaultdict

from numpy.random import randint

app = FastAPI()

class Req(BaseModel):
    prompt: str

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

active_jobs = set() 
toks_per_job = defaultdict(asyncio.Queue)

@app.post("/submit_job")
async def submitJob(req: Req, bg_tasks: BackgroundTasks):
    job_id = randint(1, 1000)   # job_id, will be displayed by the client.
    active_jobs.add(job_id)
    bg_tasks.add_task(generate, job_id, randint(1, 11)) # this is scheduled after the ack is sent.
    return {"received": job_id, "msg":req.prompt}

async def generate(job_id: int, reply_id: int):

    fake_tokens = llm_things[str(reply_id)].split()
    c = 0  # counter will be used later 

    await asyncio.sleep(0.5)  

    # this guarentees that the span exists #
    # otherwise, need a buffer if the  span is not ready yet #
    # please change to a buffer soon #

    for tok in fake_tokens:
        await toks_per_job[job_id].put((tok, c))
        c += 1
        await asyncio.sleep(0.2)
    await toks_per_job[job_id].put(("EOS",c))

async def fan_in():
    if active_jobs:
        job_id = random.choice(list(active_jobs))
        tok, id = await toks_per_job[job_id].get()
        if tok == "EOS":
            active_jobs.remove(job_id)
        yield job_id, tok, id
    else:
        await asyncio.sleep(0.5)


## you got some shit SSE syntax here:
@app.get("/events")
def stream():
    async def stream_():
        yield f": connected\n\n"
        while True:
            async for job, tok, id in fan_in():
                if tok == "EOS":
                    data = {"job_id": job}
                    yield f"event: job_complete\n"
                    yield f"data: {json.dumps(data)}\n\n"
                else:
                    data = {"job_id": job, "token": f"{tok} "}
                    yield f"event: token\n"
                    yield f"data: {json.dumps(data)}\n\n"
            
    return StreamingResponse(stream_(), media_type="text/event-stream")


app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def index():
    return FileResponse("static/index.html")