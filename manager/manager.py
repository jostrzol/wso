from __future__ import annotations
import asyncio
from datetime import datetime
import logging
import random
from typing import Any, Iterable, cast
from uuid import uuid4

from pydantic import BaseModel, Field, UUID4

from heart.heart import Heart
from manager.vmm import VMManager

from .config import Config, ManagerConfig
from .plan import Plan, VMConfig
from .repository import repository
from .settings import settings


logger = logging.getLogger("uvicorn")


class Manager:
    def __init__(self, name: str, config: Config, plan: Plan):
        self._name = name
        self._config = config
        self._plan = plan
        self._statuses: dict[UUID4, ConnectionStatus] = {}
        self._hearts: dict[UUID4, Heart] = {}
        self._vmm = VMManager(self.my_config)

    @classmethod
    async def create(cls, name: str):
        config = await repository.get_config()
        plan = await repository.get_plan()
        manager = cls(name=name, config=config, plan=plan)
        plan = await manager._on_config_changed(config)
        await manager._on_plan_changed(plan)
        return manager

    def hearbeat(self, token: UUID4) -> bool:
        status = self._statuses.get(token)
        if status is None:
            return False
        status.last_beat_at = datetime.now()
        return True

    def manager_statuses(self) -> Iterable[tuple[ManagerConfig, ConnectionStatus]]:
        for manager in self.other_managers():
            status = self.status(manager.token)
            if status is None:
                continue
            yield (manager, status)

    def vm_statuses(self) -> Iterable[tuple[VMConfig, ConnectionStatus]]:
        for vm in self.my_vms():
            status = self.status(vm.token)
            if status is None:
                continue
            yield (vm, status)

    def status(self, token: UUID4) -> ConnectionStatus | None:
        status = self._statuses.get(token)
        if status is None:
            return None
        if not status.is_dead and status.last_beat_at:
            dead_since = status.last_beat_at + self._config.general.max_inactive
            if dead_since < datetime.now():
                status = status.model_copy(update={"dead_since": dead_since})
                self._statuses[token] = status
        return status

    async def background_loop(self):
        await asyncio.gather(
            self._watch_plan_changes(),
            self._watch_config_changes(),
            self._correct_plans_forever(),
        )

    async def _correct_plans_forever(self):
        while True:
            try:
                await self._correct_plans_once()
            except Exception:
                logger.exception("correcting plans")
            await asyncio.sleep(1)

    async def _correct_plans_once(self):
        for vm in self.my_vms():
            status = self.status(vm.token)
            if status is None:
                continue
            if status.is_dead:
                logger.error(f"VM {vm.name} is dead")
                new_plan = self._remake_plan(self._plan.with_vm_removed(vm.name))
                await repository.save_plan(new_plan)
        for manager in self.other_managers():
            status = self.status(manager.token)
            if status is None:
                continue
            if status.is_dead:
                logger.error(f"Manager {manager.name} #{manager.token} is dead")

    async def _watch_plan_changes(self):
        async for plan in repository.watch_plan():
            try:
                logger.info(f"plan changed, current version: {plan.version}")
                await self._on_plan_changed(plan)
            except Exception:
                logger.exception("handling plan change")

    async def _watch_config_changes(self):
        async for config in repository.watch_config():
            try:
                logger.info("config changed")
                await self._on_config_changed(config, self._config)
            except Exception:
                logger.exception("handling config change")

    async def _on_config_changed(
        self, config: Config, old_config: Config | None = None
    ) -> Plan:
        self._config = config
        self._vmm.config = self.my_config

        old_managers = old_config.managers if old_config else None
        self._on_managers_changed(config.managers, old_managers)

        new_plan = self._remake_plan(self._plan)
        if new_plan is not self._plan:
            did_change = await repository.save_plan(new_plan)
            if did_change:
                return new_plan
        return self._plan

    def _on_managers_changed(
        self,
        new_managers: list[ManagerConfig],
        old_managers: list[ManagerConfig] | None = None,
    ):
        if old_managers is None:
            old_managers = []
        new_managers = list(self.other_managers(new_managers))
        old_managers = list(self.other_managers(old_managers))
        old_mgr_tokens = {mgr.token for mgr in old_managers}
        new_mgr_tokens = {mgr.token for mgr in new_managers}
        to_create = [mgr for mgr in new_managers if mgr.token not in old_mgr_tokens]
        to_delete = [mgr for mgr in old_managers if mgr.token not in new_mgr_tokens]
        for mgr in to_create:
            self._statuses[mgr.token] = ConnectionStatus()
            logger.info(f"making heart {mgr.token}")
            heart = Heart(mgr.host, str(self.token))
            asyncio.create_task(heart.beat_until(lambda: mgr.token in self._hearts))
            self._hearts[mgr.token] = heart
        for mgr in to_delete:
            self._statuses.pop(mgr.token, None)
            self._hearts.pop(mgr.token, None)

    def _remake_plan(self, current_plan: Plan) -> Plan:
        new_vms: list[VMConfig] = []
        changed = False
        for service in self._config.services:
            vms = current_plan.for_service(service.name)
            delta = service.replicas - len(vms)
            if delta != 0:
                changed = True
            for _ in range(delta):
                manager = self._assign_host_for_new_vm()
                ips_taken = (vm.address for vm in [*vms, *new_vms, *current_plan.vms])
                ip = manager.address_pool.generate_one_not_in(ips_taken)
                vms.append(
                    VMConfig(
                        service=service.name,
                        manager=manager.name,
                        address=ip,
                        token=uuid4(),
                    )
                )
            for _ in range(-delta):
                to_remove = self._choose_vm_to_delete(vms)
                vms.remove(to_remove)
            new_vms += vms

        new_version = current_plan.version + 1
        return Plan(version=new_version, vms=new_vms) if changed else self._plan

    def _assign_host_for_new_vm(self) -> ManagerConfig:
        return random.choice(self._config.managers)

    def _choose_vm_to_delete(self, vms: list[VMConfig]) -> VMConfig:
        return random.choice(vms)

    async def _on_plan_changed(self, plan: Plan):
        self._plan = plan
        new_vms = list(self.my_vms(plan.vms))
        old_vms = list(self._vmm.list_current_vms())
        new_vm_names = {vm.name for vm in new_vms}
        old_vm_names = {vm.name for vm in old_vms}
        to_create = [vm for vm in new_vms if vm.name not in old_vm_names]
        to_delete = [vm for vm in old_vms if vm.name not in new_vm_names]
        await asyncio.gather(
            *(self._create_vm(vm) for vm in to_create),
            *(self._delete_vm(vm) for vm in to_delete),
        )
        self._statuses = {
            vm.token: ConnectionStatus() for vm in new_vms
        } | self._statuses

    async def _create_vm(self, vm: VMConfig):
        logger.info(f"starting VM {vm.name}")
        await self._vmm.create_new_vm(vm)
        logger.info(f"VM {vm.name} active")
        await self._vmm._start_timesrv(vm)
        status = ConnectionStatus()
        self._statuses[vm.token] = status

    async def _delete_vm(self, vm: VMConfig):
        def impl():
            logger.info(f"deleting VM {vm.name}")
            self._vmm.delete_vm(vm.name)

        await asyncio.to_thread(impl)
        self._statuses.pop(vm.token, None)

    @property
    def token(self) -> UUID4:
        return self.my_config.token

    @property
    def my_config(self) -> ManagerConfig:
        for manager in self._config.managers:
            if manager.name == settings.manager_name:
                return manager
        raise Exception(f"manager '{settings.manager_name}' not in config")

    def other_managers(
        self, mgrs: Iterable[ManagerConfig] | None = None
    ) -> Iterable[ManagerConfig]:
        if mgrs is None:
            mgrs = self._config.managers
        yield from (mgr for mgr in mgrs if mgr.name != self._name)

    def my_vms(self, vms: Iterable[VMConfig] | None = None) -> Iterable[VMConfig]:
        if vms is None:
            vms = self._plan.vms
        yield from (vm for vm in vms if vm.manager == self._name)


class ConnectionStatus(BaseModel):
    planned_at: datetime = Field(default_factory=datetime.now)
    created_at: datetime | None = None
    last_beat_at: datetime | None = None
    dead_since: datetime | None = None

    @property
    def last_beat_before(self):
        return datetime.now() - self.last_beat_at if self.last_beat_at else None

    @property
    def is_dead(self):
        return self.dead_since is not None


manager: Manager = cast(Any, None)
