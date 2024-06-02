from __future__ import annotations
from ipaddress import IPv4Address
from typing import Annotated, Literal

from pydantic import BaseModel, Field, UUID4


class Plan(BaseModel):
    version: int = 0
    vms: list[Vm] = Field(default_factory=lambda: [])

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
