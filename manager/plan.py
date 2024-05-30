from __future__ import annotations

from pydantic import BaseModel, Field, IPvAnyAddress, UUID4


class Plan(BaseModel):
    version: int = 0
    vms: list[VMConfig] = Field(default_factory=lambda: [])

    def for_service(self, service_name: str) -> list[VMConfig]:
        return [vm for vm in self.vms if vm.service == service_name]


class VMConfig(BaseModel):
    service: str
    manager: str
    address: IPvAnyAddress
    token: UUID4
