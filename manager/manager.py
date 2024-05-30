from __future__ import annotations
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import random
from typing import Any, Iterator, cast
from uuid import uuid4

from pydantic import UUID4

from .config import Config, ManagerConfig
from .plan import Plan, VMConfig
from .repository import repository

logger = logging.getLogger("uvicorn")


class Manager:
    def __init__(self, name: str, config: Config, plan: Plan):
        self._name = name
        self._config = config
        self._plan = plan
        self._update_last_hearbeats()

    @classmethod
    async def create(cls, name: str):
        config = await repository.get_config()
        plan = await repository.get_plan()
        manager = cls(name=name, config=config, plan=plan)
        await manager._replan()
        return manager

    @property
    def config(self):
        return self._config

    def hearbeat(self, token: UUID4):
        self._last_hearbeats[token] = datetime.now()

    def connection_status(self, token: UUID4) -> ConnectionStatus:
        last_beat = self._last_hearbeats[token]
        last_beat_before = datetime.now() - last_beat
        is_inactive = last_beat_before > self._config.general.max_inactive
        return ConnectionStatus(
            last_beat_at=last_beat,
            last_beat_before=last_beat_before,
            is_dead=is_inactive,
        )

    async def execute_plan_forever(self):
        while True:
            for vm in self.my_vms():
                status = self.connection_status(vm.token)
                if status.is_dead:
                    logger.error(f"VM #{vm.token} is dead")
            for manager in self.other_managers():
                status = self.connection_status(manager.token)
                if status.is_dead:
                    logger.error(f"Manager {manager.name} #{manager.token} is dead")
            await asyncio.sleep(1)

    async def watch_changes_forever(self):
        asyncio.create_task(self._watch_plan_changes())
        asyncio.create_task(self._watch_config_changes())

    async def _watch_plan_changes(self):
        async for plan in repository.watch_plan():
            logger.info(f"plan changed, current version: {plan.version}")
            self._assign_plan(plan)

    async def _watch_config_changes(self):
        async for config in repository.watch_config():
            logger.info("config changed")
            self._config = config
            await self._replan()
            self._update_last_hearbeats()

    async def _replan(self):
        new_plan = self._make_new_plan()
        if new_plan is not self._plan:
            await repository.save_plan(new_plan)
            self._assign_plan(new_plan)

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

        new_version = self._plan.version + 1
        return Plan(version=new_version, vms=new_vms) if changed else self._plan

    def _assign_host_for_new_vm(self) -> ManagerConfig:
        return random.choice(self._config.managers)

    def _choose_vm_to_delete(self, vms: list[VMConfig]) -> VMConfig:
        return random.choice(vms)

    def _assign_plan(self, plan: Plan):
        self._plan = plan
        self._update_last_hearbeats()

    def _update_last_hearbeats(self):
        tokens = self._relevant_tokens()
        now = datetime.now()
        if not hasattr(self, "_last_hearbeats"):
            self._last_hearbeats = {}
        self._last_hearbeats = {token: now for token in tokens} | self._last_hearbeats

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


@dataclass
class ConnectionStatus:
    last_beat_at: datetime
    last_beat_before: timedelta
    is_dead: bool


manager: Manager = cast(Any, None)
