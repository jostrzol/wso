from datetime import datetime

from pydantic import UUID4

from .config import Config


class Manager:
    def __init__(self, config: Config):
        self._config = config
        tokens = [
            *(manager.token for manager in self._config.managers),
            *(vm.token for vm in self._config.vms),
        ]
        now = datetime.now()
        self._last_hearbeats = {token: now for token in tokens}

    def hearbeat(self, token: UUID4):
        self._last_hearbeats[token] = datetime.now()
