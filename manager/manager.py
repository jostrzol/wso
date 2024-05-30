from datetime import datetime
from typing import Any, Iterator, cast

from pydantic import UUID4

from .vms import VMConfig, VMsConfig
from .config import Config, ManagerConfig
from .repository import repository


class Manager:
    def __init__(self, name: str, config: Config, vms: VMsConfig):
        self._name = name
        self._config = config
        self._vms = vms

        tokens = self._relevant_tokens()
        now = datetime.now()
        self._last_hearbeats = {token: now for token in tokens}

    @classmethod
    async def create(cls, name: str):
        config = await repository.get_config()
        vms = await repository.get_vms_config()
        return cls(name=name, config=config, vms=vms)

    @property
    def config(self):
        return self._config

    def _relevant_tokens(self) -> Iterator[UUID4]:
        yield from (
            *(manager.token for manager in self.other_managers()),
            *(vm.token for vm in self.my_vms()),
        )

    def other_managers(self) -> Iterator[ManagerConfig]:
        yield from (
            manager for manager in self._config.managers if manager.name != self._name
        )

    def my_vms(self) -> Iterator[VMConfig]:
        yield from (vm for vm in self._vms.vms if vm.manager == self._name)

    def hearbeat(self, token: UUID4):
        self._last_hearbeats[token] = datetime.now()

    def last_heartbeat(self, token: UUID4) -> datetime:
        return self._last_hearbeats[token]


manager: Manager = cast(Any, None)
