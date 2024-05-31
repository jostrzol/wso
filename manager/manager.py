from __future__ import annotations
import asyncio
from datetime import datetime
import logging
import random
import re
import subprocess
from typing import Any, Iterable, cast
from uuid import uuid4

import libvirt
from pydantic import BaseModel, Field, UUID4

from .config import Config, ManagerConfig
from .plan import Plan, VMConfig
from .repository import repository
from .settings import settings
from .utils import generate_timesrv_xml

logger = logging.getLogger("uvicorn")


class Manager:
    def __init__(self, name: str, config: Config, plan: Plan):
        self._name = name
        self._config = config
        self._plan = plan
        self._statuses: dict[UUID4, ConnectionStatus] = {}
        self._conn = libvirt.open("qemu:///system")

    @classmethod
    async def create(cls, name: str):
        config = await repository.get_config()
        plan = await repository.get_plan()
        manager = cls(name=name, config=config, plan=plan)
        plan = await manager._on_config_changed(config)
        await manager._on_plan_changed(plan)
        return manager

    @property
    def config(self):
        return self._config

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

    async def correct_plans_forever(self):
        while True:
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
            await asyncio.sleep(1)

    async def watch_changes_forever(self):
        await asyncio.gather(self._watch_plan_changes(), self._watch_config_changes())

    async def _watch_plan_changes(self):
        async for plan in repository.watch_plan():
            logger.info(f"plan changed, current version: {plan.version}")
            await self._on_plan_changed(plan)

    async def _watch_config_changes(self):
        async for config in repository.watch_config():
            logger.info("config changed")
            await self._on_config_changed(config)

    async def _on_config_changed(
        self, config: Config, old_config: Config | None = None
    ) -> Plan:
        self._config = config
        old_managers = old_config.managers if old_config else None
        self._update_manager_statuses(config.managers, old_managers)

        new_plan = self._remake_plan(self._plan)
        if new_plan is not self._plan:
            did_change = await repository.save_plan(new_plan)
            if did_change:
                return new_plan
        return self._plan

    def _update_manager_statuses(
        self,
        new_managers: list[ManagerConfig],
        old_managers: list[ManagerConfig] | None = None,
    ):
        if old_managers is None:
            old_managers = []
        old_mgr_tokens = {mgr.token for mgr in old_managers}
        new_mgr_tokens = {mgr.token for mgr in new_managers}
        to_create = [mgr for mgr in new_managers if mgr.token not in old_mgr_tokens]
        to_delete = [mgr for mgr in old_managers if mgr.token not in new_mgr_tokens]
        for mgr in to_create:
            self._statuses[mgr.token] = ConnectionStatus()
        for mgr in to_delete:
            self._statuses.pop(mgr.token, None)

    def _remake_plan(self, current_plan: Plan) -> Plan:
        new_vms = []
        changed = False
        print(self._config.services)
        for service in self._config.services:
            vms = current_plan.for_service(service.name)
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

        new_version = current_plan.version + 1
        return Plan(version=new_version, vms=new_vms) if changed else self._plan

    def _assign_host_for_new_vm(self) -> ManagerConfig:
        return random.choice(self._config.managers)

    def _choose_vm_to_delete(self, vms: list[VMConfig]) -> VMConfig:
        return random.choice(vms)

    async def _on_plan_changed(self, plan: Plan):
        self._plan = plan
        new_vms = list(self.my_vms(plan.vms))
        old_vms = list(self.list_current_vms())
        print(old_vms)
        print(new_vms)
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
        def impl():
            logger.info(f"starting VM {vm.name}")
            self.create_new_vm(vm.name, vm.service)
            logger.info(f"VM {vm.name} active")

        await asyncio.to_thread(impl)
        status = ConnectionStatus()
        self._statuses[vm.token] = status

    async def _delete_vm(self, vm: VMConfig):
        def impl():
            logger.info(f"deleting VM {vm.name}")
            self.delete_vm(vm.name)

        await asyncio.to_thread(impl)
        self._statuses.pop(vm.token, None)

    def other_managers(self) -> Iterable[ManagerConfig]:
        yield from (
            manager for manager in self._config.managers if manager.name != self._name
        )

    @property
    def my_config(self) -> ManagerConfig:
        for manager in self._config.managers:
            if manager.name == settings.manager_name:
                return manager
        raise Exception(f"manager '{settings.manager_name}' not in config")

    @property
    def imgs_path(self) -> str:
        return str(self.my_config.imgs_path)

    def my_vms(self, vms: Iterable[VMConfig] | None = None) -> Iterable[VMConfig]:
        if vms is None:
            vms = self._plan.vms
        yield from (vm for vm in vms if vm.manager == self._name)

    VM_NAME_REGEX = re.compile(
        r"^wso-(.*)-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
    )

    def list_current_vms(self) -> Iterable[VMConfig]:
        for domain in self._conn.listAllDomains():
            match = re.match(self.VM_NAME_REGEX, domain.name())
            if match is not None:
                service, token = match.groups()
                yield VMConfig(
                    service=service,
                    manager=self.my_config.name,
                    address=cast(Any, "127.0.0.1"),
                    token=UUID4(token),
                )

    def get_ip(self, domain_name: str):
        domain = self._conn.lookupByName(domain_name)
        ifaces = domain.interfaceAddresses(
            libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT, 0
        )

        return ifaces["eth0"]["addrs"][0]["addr"]

    def create_new_vm(self, name: str, service: str):
        if service == "timesrv":
            self._create_timesrv_vm(name)

    def _create_timesrv_vm(self, name: str):
        subprocess.run(
            ["cp", f"{self.imgs_path}/timesrv.qcow2", f"{self.imgs_path}/{name}.qcow2"],
            check=True,
        )

        try:
            self._conn.createXML(generate_timesrv_xml(self.imgs_path, name), 0)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to create VM: {e}")

    def delete_vm(self, name: str):
        try:
            dom = self._conn.lookupByName(name)

            if dom.isActive():
                dom.destroy()

            subprocess.run(["rm", f"{self.imgs_path}/{name}.qcow2"], check=True)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to delete VM: {e}")


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
