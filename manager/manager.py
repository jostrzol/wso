from datetime import datetime
import random
from typing import Any, Iterator, cast
from uuid import uuid4

from pydantic import UUID4

from .config import Config, ManagerConfig
from .plan import Plan, VMConfig
from .repository import repository


class Manager:
    def __init__(self, name: str, config: Config, plan: Plan):
        self._name = name
        self._config = config
        self._plan = plan

        tokens = self._relevant_tokens()
        now = datetime.now()
        self._last_hearbeats = {token: now for token in tokens}

    @classmethod
    async def create(cls, name: str):
        config = await repository.get_config()
        plan = await repository.get_plan()
        return cls(name=name, config=config, plan=plan)

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
        yield from (vm for vm in self._plan.vms if vm.manager == self._name)

    def hearbeat(self, token: UUID4):
        self._last_hearbeats[token] = datetime.now()

    def last_heartbeat(self, token: UUID4) -> datetime:
        return self._last_hearbeats[token]

    async def watch_changes_forever(self):
        await self._replan()

    async def _replan(self):
        new_plan = self._make_new_plan()
        if new_plan is not self._plan:
            await repository.save_plan(new_plan)

    def _make_new_plan(self) -> Plan:
        new_vms = []
        changed = False
        for service in self._config.services:
            vms = self._plan.for_service(service.name)
            delta = service.replicas - len(vms)
            if delta != 0:
                changed = True
            for _ in range(delta):
                manager = self._assign_host_for_new_vm()
                vms.append(
                    VMConfig(
                        service=service.name,
                        manager=manager.name,
                        address=cast(Any, "127.0.0.1"),
                        token=uuid4(),
                    )
                )
            for _ in range(-delta):
                to_remove = self._choose_vm_to_delete(vms)
                vms.remove(to_remove)
            new_vms += vms
        return Plan(version=self._plan.version, vms=new_vms) if changed else self._plan

    def _assign_host_for_new_vm(self) -> ManagerConfig:
        return random.choice(self._config.managers)

    def _choose_vm_to_delete(self, vms: list[VMConfig]) -> VMConfig:
        return random.choice(vms)


manager: Manager = cast(Any, None)
