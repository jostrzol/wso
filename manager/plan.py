from __future__ import annotations
from ipaddress import IPv4Address

from pydantic import BaseModel, Field, UUID4


class Plan(BaseModel):
    version: int = 0
    vms: list[VMConfig] = Field(default_factory=lambda: [])

    def for_service(self, service_name: str) -> list[VMConfig]:
        return [vm for vm in self.vms if vm.service == service_name]

    def with_vm_removed(self, vm_name: str) -> Plan:
        new_vms = filter(lambda vm: vm.name != vm_name, self.vms)
        return self.model_copy(update={"vms": new_vms})


class VMConfig(BaseModel):
    service: str
    manager: str
    address: IPv4Address
    token: UUID4

    @property
    def name(self):
        return f"wso-{self.service}-{self.manager}-{self.token}"
