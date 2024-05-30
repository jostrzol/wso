import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
import logging

from fastapi import FastAPI
from websockets import client


MANAGER_ADDRESS = (
    "ws://127.0.0.1:8000/vms/22a119cf-0bf3-4fb0-8c13-bd452a03432d/heartbeat"
)
HEARTBEAT_INTERVAL = 1


async def heartbeat_task():
    logger = logging.getLogger("uvicorn")
    async with client.connect(MANAGER_ADDRESS) as websocket:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await websocket.send("")


@asynccontextmanager
async def lifespan(_: FastAPI):
    asyncio.create_task(heartbeat_task())
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/time")
async def get_time() -> datetime:
    return datetime.now()
