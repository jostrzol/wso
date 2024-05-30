import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI

from heart.heart import Heart
from .settings import settings


@asynccontextmanager
async def lifespan(_: FastAPI):
    heart = Heart(manager_address=settings.manager_address, token=settings.token)
    asyncio.create_task(heart.beat_forever())
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/time")
async def get_time() -> datetime:
    return datetime.now()
