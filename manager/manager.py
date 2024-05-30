from datetime import datetime
from typing import Generator, NoReturn

from pydantic import UUID4

from .config import Config, ManagerConfig, VMConfig


class Manager:
    def __init__(self, name: str, config: Config):
        self._name = name
        self._config = config

        tokens = self._relevant_tokens()
        now = datetime.now()
        self._last_hearbeats = {token: now for token in tokens}

    @property
    def config(self):
        return self._config

    def _relevant_tokens(self) -> Generator[UUID4, NoReturn, None]:
        yield from (
            *(manager.token for manager in self.other_managers()),
            *(vm.token for vm in self.my_vms()),
        )

    def other_managers(self) -> Generator[ManagerConfig, NoReturn, None]:
        yield from (
            manager for manager in self._config.managers if manager.name != self._name
        )

    def my_vms(self) -> Generator[VMConfig, NoReturn, None]:
        yield from (vm for vm in self._config.vms if vm.manager == self._name)

    def hearbeat(self, token: UUID4):
        self._last_hearbeats[token] = datetime.now()

    def last_heartbeat(self, token: UUID4) -> datetime:
        return self._last_hearbeats[token]
