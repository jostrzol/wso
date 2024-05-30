from __future__ import annotations

from pydantic import BaseModel, IPvAnyAddress, UUID4


class VMsConfig(BaseModel):
    version: int
    vms: list[VMConfig]


class VMConfig(BaseModel):
    service: str
    manager: str
    address: IPvAnyAddress
    token: UUID4
