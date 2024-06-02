from __future__ import annotations
import asyncio
from datetime import datetime
import logging
from math import ceil
import random
from typing import Any, Iterable, cast
from uuid import uuid4
import uuid

from pydantic import BaseModel, Field, UUID4

from heart.heart import Heart
from manager.vmm import VMManager

from .config import Config, LBConfig, ManagerConfig, ServiceConfig
from .plan import LoadBalancer, ManagerState, Plan, Vm, Worker
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
        asyncio.create_task(manager._on_plan_changed(plan))
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

    def vm_statuses(self) -> Iterable[tuple[Vm, ConnectionStatus]]:
        for vm in self.my_vms():
            status = self.status(vm.token)
            if status is None:
                continue
            yield (vm, status)

    def status(self, token: UUID4) -> ConnectionStatus | None:
        return self._statuses.get(token)

    def _update_statuses(self) -> dict[UUID4, ConnectionStatus]:
        changed_statuses: dict[UUID4, ConnectionStatus] = {}
        for token, status in self._statuses.items():
            if status.last_beat_at:
                dead_since = status.last_beat_at + self._config.general.max_inactive
                if dead_since < datetime.now() and not status.is_dead:
                    status = status.model_copy(update={"dead_since": dead_since})
                    self._statuses[token] = status
                    changed_statuses[token] = status
                elif dead_since >= datetime.now() and status.is_dead:
                    status = status.model_copy(update={"dead_since": None})
                    self._statuses[token] = status
                    changed_statuses[token] = status
        return changed_statuses

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
        changed_statuses = self._update_statuses()
        for vm in self.my_vms():
            status = changed_statuses.get(vm.token)
            if status is None:
                continue
            if status.is_dead:
                logger.error(f"VM {vm.name} became dead")
                removed = self._plan.with_vm_removed(vm.name)
                new_plan = self._remake_plan(removed)
                await repository.save_plan(new_plan)
            else:
                logger.error(f"VM {vm.name} became alive")
        for manager in self.other_managers():
            status = changed_statuses.get(manager.token)
            if status is None:
                continue
            if status.is_dead:
                logger.error(f"Manager {manager.name} #{manager.token} became dead")
                new_plan = self._remake_plan(self._plan)
                await repository.save_plan(new_plan)
            else:
                logger.info(f"Manager {manager.name} #{manager.token} became alive")
                new_plan = self._remake_plan(self._plan)
                await repository.save_plan(new_plan)

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
        new_vms: list[Vm] = []
        changed = False

        new_mgr_states, changed_mgr_states = self._remake_manager_states(current_plan)
        changed = changed or changed_mgr_states
        active_managers = [
            mgr
            for mgr, state in zip(self._config.managers, new_mgr_states)
            if state.is_active
        ]

        for service, lb_config in self._config.service_lb_pairs():
            workers, workers_changed = self._remake_service_workers(
                service,
                current_plan,
                new_vms,
                active_managers,
            )
            lb, lb_changed = self._remake_service_lb(
                service,
                lb_config,
                current_plan,
                workers,
                active_managers,
            )
            new_vms += workers + lb
            changed = changed or workers_changed or lb_changed

        new_version = current_plan.version + 1
        return (
            Plan(version=new_version, vms=new_vms, manager_states=new_mgr_states)
            if changed
            else self._plan
        )

    def _remake_manager_states(
        self, current_plan: Plan
    ) -> tuple[list[ManagerState], bool]:
        changed = False

        primary: ManagerState | None = None
        new_manager_names: list[str] = [
            manager.name
            for manager in self._config.managers
            if current_plan.state_for(manager.name) is None
        ]
        if len(new_manager_names) != 0:
            changed = True

        new_manager_states: list[ManagerState] = [
            state.model_copy(deep=True) for state in current_plan.manager_states
        ] + [ManagerState(name=name) for name in new_manager_names]

        for manager, state in zip(self._config.managers, new_manager_states):
            status = self.status(manager.token)
            is_dead_for_me = status is not None and status.is_dead
            if is_dead_for_me and self._name not in state.is_dead_for:
                changed = True
                state.is_dead_for.add(self._name)
            elif not is_dead_for_me and self._name in state.is_dead_for:
                changed = True
                state.is_dead_for.remove(self._name)

        match [state for state in new_manager_states if state.is_primary]:
            case [state]:
                primary = state
            case _:
                primary = None

        to_shuffle = list(new_manager_states)
        random.shuffle(to_shuffle)
        least_dead = min(
            to_shuffle,
            key=lambda state: (len(state.is_dead_for), state.name != self._name),
        )

        quorum = int(ceil(len(self._config.managers) / 2))
        if primary is None:
            changed = True
            primary = least_dead
            primary.is_primary = True
        elif primary.is_dead(quorum) and not least_dead.is_dead(quorum):
            changed = True
            primary.is_primary = False
            primary = least_dead
            primary.is_primary = True

        new_states_dict = {state.name: state for state in new_manager_states}
        visited: set[str] = set()
        to_visit: set[str] = {primary.name}
        while len(to_visit) != 0:
            state = new_states_dict[to_visit.pop()]
            visited.add(state.name)
            if not state.is_active:
                changed = True
                state.is_active = True
            for other_state in new_manager_states:
                if state.name in other_state.is_dead_for:
                    continue
                if other_state.name in visited:
                    continue
                to_visit.add(other_state.name)

        for state in new_manager_states:
            if state.name in visited:
                continue
            if state.is_active:
                changed = True
                state.is_active = False

        return new_manager_states, changed

    def _remake_service_workers(
        self,
        service: ServiceConfig,
        current_plan: Plan,
        new_vms: Iterable[Vm],
        active_managers: list[ManagerConfig],
    ) -> tuple[list[Vm], bool]:
        changed = False
        active_manager_names = {manager.name for manager in active_managers}
        workers = [
            worker
            for worker in current_plan.workers_for_service(service.name)
            if worker.manager in active_manager_names
        ]
        delta = service.replicas - len(workers)
        if delta != 0:
            changed = True
        for _ in range(delta):
            manager = self._assign_host_for_new_vm(active_managers)
            ips_taken = (vm.address for vm in [*workers, *new_vms, *current_plan.vms])
            ip = manager.address_pool.generate_one_not_in(ips_taken)
            workers.append(
                Worker(
                    service=service.name,
                    manager=manager.name,
                    address=ip,
                    port=service.port,
                    token=uuid4(),
                )
            )
        for _ in range(-delta):
            to_remove = self._choose_vm_to_delete(workers)
            workers.remove(to_remove)
        return workers, changed

    def _assign_host_for_new_vm(
        self, active_managers: list[ManagerConfig]
    ) -> ManagerConfig:
        return random.choice(active_managers)

    def _choose_vm_to_delete(self, vms: list[Vm]) -> Vm:
        return random.choice(vms)

    def _remake_service_lb(
        self,
        service: ServiceConfig,
        lb_config: LBConfig | None,
        current_plan: Plan,
        workers: Iterable[Vm],
        active_managers: list[ManagerConfig],
    ) -> tuple[list[LoadBalancer], bool]:
        lb_vm = current_plan.lb_for_service(service.name)
        upstream = [(vm.address, vm.port) for vm in workers]

        if lb_config is None:
            return [], lb_vm is not None

        if lb_vm is None:
            # create new vm
            manager = self._assign_host_for_new_vm(active_managers)
            new_lb = LoadBalancer(
                service=lb_config.service,
                manager=manager.name,
                address=lb_config.address,
                port=lb_config.port,
                token=uuid4(),
                upstream=upstream,
            )
            return [new_lb], True

        # check if old vm modified
        active_manager_names = {manager.name for manager in active_managers}
        manager = (
            lb_vm.manager
            if lb_vm.manager in active_manager_names
            else self._assign_host_for_new_vm(active_managers).name
        )

        new_fields = {
            "upstream": upstream,
            "port": lb_config.port,
            "address": lb_config.address,
            "manager": manager,
        }
        old_fields = lb_vm.model_dump(include=set(new_fields.keys()))
        diffs = {key: new_fields[key] != old_fields[key] for key in new_fields.keys()}
        if any(diffs.values()):
            requires_rebuild = ["address", "manager"]
            if any(diffs[key] for key in requires_rebuild):
                new_fields["token"] = uuid4()
            new_lb_vm = lb_vm.model_copy(update=new_fields)
            return [new_lb_vm], True
        else:
            return [lb_vm], False

    async def _on_plan_changed(self, plan: Plan):
        new_vms = list(self.my_vms(plan.vms))
        old_vms = list(self.my_vms(self._plan.vms, with_libvirt_query=True))
        self._plan = plan
        new_vm_names = {vm.name for vm in new_vms}
        old_vm_names = {vm.name for vm in old_vms}

        to_create = [vm for vm in new_vms if vm.name not in old_vm_names]
        to_delete = [vm for vm in old_vms if vm.name not in new_vm_names]

        old_lbs = {vm.name: vm for vm in old_vms if isinstance(vm, LoadBalancer)}
        lbs_to_update = [
            vm
            for vm in new_vms
            if isinstance(vm, LoadBalancer)
            if vm.name in old_lbs and vm.model_dump() != old_lbs[vm.name].model_dump()
        ]

        await asyncio.gather(
            *(self._create_vm(vm) for vm in to_create),
            *(self._delete_vm(vm) for vm in to_delete),
            *(self._update_lb(lb) for lb in lbs_to_update),
        )
        self._statuses = {
            vm.token: ConnectionStatus() for vm in new_vms
        } | self._statuses

    async def _create_vm(self, vm: Vm):
        match vm:
            case Worker():
                config = self._service_for(vm)
            case LoadBalancer() as lb:
                config = self._lb_config_for(lb)
        logger.info(f"starting VM {vm.name}")
        await self._vmm.create_new_vm(vm, config)
        logger.info(f"VM {vm.name} active")
        status = ConnectionStatus()
        self._statuses[vm.token] = status

    def _service_for(self, vm: Vm) -> ServiceConfig:
        for service in self._config.services:
            if service.name == vm.service:
                return service
        raise KeyError(f"service '{vm.service}' for VM {vm.name} not found")

    def _lb_config_for(self, lb: LoadBalancer) -> LBConfig:
        for config in self._config.load_balancers:
            if config.service == lb.service:
                return config
        raise KeyError(f"load balancer config for service '{lb.service}'")

    async def _delete_vm(self, vm: Vm):
        logger.info(f"deleting VM {vm.name}")
        await self._vmm.delete_vm(vm.name)
        self._statuses.pop(vm.token, None)

    async def _update_lb(self, lb: LoadBalancer):
        logger.info(f"updating upstream of LB {lb.name}")
        await self._vmm.setup_nginx(lb)

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

    def my_vms(
        self, vms: Iterable[Vm] | None = None, with_libvirt_query: bool = False
    ) -> Iterable[Vm]:
        if vms is None:
            vms = self._plan.vms
        vms = [vm for vm in vms if vm.manager == self._name]
        yield from vms
        if with_libvirt_query:
            used_names = {vm.name for vm in vms}
            yield from (
                vm for vm in self._vmm.list_current_vms() if vm.name not in used_names
            )


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
