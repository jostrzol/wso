from datetime import datetime
from typing import Generator, NoReturn

from pydantic import UUID4

from .config import Config


class Manager:
    def __init__(self, name: str, config: Config):
        self._name = name
        self._config = config

        tokens = self._relevant_tokens()
        now = datetime.now()
        self._last_hearbeats = {token: now for token in tokens}

    def _relevant_tokens(self) -> Generator[UUID4, NoReturn, None]:
        yield from (
            *self._other_manager_ids(),
            *self._my_vm_ids(),
        )

    def _other_manager_ids(self) -> Generator[UUID4, NoReturn, None]:
        yield from (
            manager.token
            for manager in self._config.managers
            if manager.name != self._name
        )

    def _my_vm_ids(self) -> Generator[UUID4, NoReturn, None]:
        yield from (
            manager.token
            for manager in self._config.managers
            if manager.name != self._name
        )

    def hearbeat(self, token: UUID4):
        self._last_hearbeats[token] = datetime.now()
