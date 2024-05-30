import asyncio
from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator

from websockets import ConnectionClosedError, WebSocketClientProtocol, client

logger = logging.getLogger("uvicorn")


class Heart:
    def __init__(
        self,
        manager_address: str,
        token: str,
        beat_interval: float = 1.0,
        reconnect_interval: float = 10.0,
    ):
        self._manager_address = manager_address
        self._token = token
        self._beat_interval = beat_interval
        self._reconnect_interval = reconnect_interval

    async def beat_forever(self):
        async for websocket in self._reconnect_forever():
            try:
                while True:
                    await self._beat(websocket)
            except ConnectionClosedError:
                logger.exception("heartbeat connection closed")

    async def _beat(self, websocket: WebSocketClientProtocol):
        await asyncio.sleep(self._beat_interval)
        await websocket.send("")

    @property
    def _ws_url(self):
        return f"ws://{self._manager_address}/heartbeats/{self._token}"

    async def _reconnect_forever(self) -> AsyncIterator[WebSocketClientProtocol]:
        while True:
            try:
                async with self._connect() as websocket:
                    yield websocket
            except Exception:
                logger.exception("could not connect heartbeat")
            await asyncio.sleep(self._reconnect_interval)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[WebSocketClientProtocol]:
        async with client.connect(self._ws_url) as websocket:
            yield websocket
