import asyncio
from io import StringIO
import logging
from typing import AsyncIterator

from motor.motor_asyncio import AsyncIOMotorChangeStream, AsyncIOMotorClient
from pydantic import MongoDsn
from pymongo.errors import PyMongoError

from .config import Config
from .plan import Plan
from .settings import settings

WATCH_ERROR_RECOVERY_INTERVAL = 10.0

logger = logging.getLogger("uvicorn")


class Repository:
    def __init__(self, connection_string: MongoDsn):
        self._host, self._port = self._parse_dsn(connection_string)
        self._client = AsyncIOMotorClient(self._host, self._port)
        self._db = self._client.get_default_database()

    @staticmethod
    def _parse_dsn(dsn: MongoDsn) -> tuple[str, int]:
        hosts = dsn.hosts()
        assert len(hosts) == 1

        host = hosts[0]
        assert host["host"] is not None
        assert host["port"] is not None

        b = StringIO()
        b.write(dsn.scheme)
        b.write("://")
        if host["username"] is not None:
            b.write(host["username"])
            if host["password"] is not None:
                b.write(":")
                b.write(host["password"])
            b.write("@")
        b.write(host["host"])
        if dsn.path is not None:
            b.write(dsn.path)
        b.seek(0)

        return (b.read(), host["port"])

    async def get_config(self) -> Config:
        obj = await self._db.configs.find_one({"_id": "global"})
        if obj is None:
            raise Exception("configuration not found; configure first with 'ctl'")
        return Config(**obj)

    async def get_plan(self) -> Plan:
        obj = await self._db.plans.find_one({"_id": "global"})
        return Plan(**obj) if obj is not None else Plan()

    async def save_plan(self, plan: Plan) -> bool:
        result = await self._db.plans.replace_one(
            {"_id": "global", "version": plan.version - 1},
            plan.model_dump(mode="json"),
            upsert=True,
        )
        return result.modified_count > 0

    async def watch_plan(self) -> AsyncIterator[Plan]:
        while True:
            try:
                async with self._db.plans.watch(full_document="required") as stream:
                    async for change in stream:
                        obj = change["fullDocument"]
                        yield Plan(**obj)
            except PyMongoError:
                logger.exception("watching for plan changes")
            await asyncio.sleep(WATCH_ERROR_RECOVERY_INTERVAL)

    async def watch_config(self) -> AsyncIterator[Config]:
        while True:
            try:
                async with self._db.configs.watch(full_document="required") as stream:
                    async for change in stream:
                        obj = change["fullDocument"]
                        yield Config(**obj)
            except PyMongoError:
                logger.exception("watching for config changes")
            await asyncio.sleep(WATCH_ERROR_RECOVERY_INTERVAL)


repository = Repository(settings.connection_string)
