from __future__ import annotations
from ipaddress import IPv4Address
from math import ceil
from typing import Annotated, Literal

from pydantic import BaseModel, Field, UUID4


class Plan(BaseModel):
    version: int = 0
    vms: list[Vm] = Field(default_factory=lambda: [])
    manager_states: list[ManagerState] = Field(default_factory=lambda: [])

    @property
    def primary_manager(self) -> str | None:
        try:
            quorum = int(ceil(len(self.manager_states) / 2))
            return next(
                state
                for state in self.manager_states
                if state.is_primary and not state.is_dead(quorum)
            ).name
        except StopIteration as e:
            raise NoPrimaryManagerError() from e

    def state_for(self, name: str) -> ManagerState | None:
        match [state for state in self.manager_states if state.name == name]:
            case [state]:
                return state
            case _:
                return None

    def workers_for_service(self, service_name: str) -> list[Vm]:
        return [
            vm
            for vm in self.vms
            if vm.service == service_name and isinstance(vm, Worker)
        ]

    def lb_for_service(self, service_name: str) -> LoadBalancer | None:
        match [vm for vm in self.vms if vm.service == service_name and vm.type == "lb"]:
            case [LoadBalancer() as lb]:
                return lb
            case _:
                return None

    def with_vm_removed(self, vm_name: str) -> Plan:
        new_vms = filter(lambda vm: vm.name != vm_name, self.vms)
        return self.model_copy(update={"vms": list(new_vms)})


VmType = Literal["wrk"] | Literal["lb"]


class VmBase(BaseModel):
    service: str
    manager: str
    address: IPv4Address
    port: int
    token: UUID4

    @property
    def name(self):
        return (
            f"wso-{self.manager}-{self.type}-{self.service}-{self.token}"  # type:ignore
        )

    @property
    def host(self):
        return f"{self.address}:{self.port}"


class Worker(VmBase):
    type: Literal["wrk"] = "wrk"  # type: ignore


class LoadBalancer(VmBase):
    type: Literal["lb"] = "lb"  # type: ignore
    upstream: list[tuple[IPv4Address, int]]


Vm = Annotated[Worker | LoadBalancer, Field(discriminator="type")]


class ManagerState(BaseModel):
    name: str
    is_primary: bool = False
    is_dead_for: set[str] = Field(default_factory=set)
    is_active: bool = True

    def is_dead(self, quorum: int) -> bool:
        return len(self.is_dead_for) >= quorum


class NoPrimaryManagerError(Exception):
    def __init__(self):
        super().__init__("no primary manager")
