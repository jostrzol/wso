from io import StringIO

from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import MongoDsn

from .config import Config
from .settings import settings
from .vms import VMsConfig


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
        obj = await self._db.configs.find_one({"_id": "config"})
        if obj is None:
            raise Exception("configuration not found; configure first with 'ctl'")
        return Config(**obj)

    async def get_vms_config(self) -> VMsConfig:
        obj = await self._db.vms.find_one({"_id": "config"})
        if obj is None:
            raise Exception("vms configuration not found; configure first with 'ctl'")
        return VMsConfig(**obj)


repository = Repository(settings.connection_string)
