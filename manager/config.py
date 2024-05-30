from __future__ import annotations

from pydantic import UUID4, BaseModel, IPvAnyAddress


class Config(BaseModel):
    managers: list[ManagerConfig]
    vms: list[VMConfig]
    services: list[ServiceConfig]


class ManagerConfig(BaseModel):
    name: str
    address: IPvAnyAddress
    token: UUID4


class ServiceConfig(BaseModel):
    name: str
    image: str
    port: int


class VMConfig(BaseModel):
    service: str
    address: IPvAnyAddress
    token: UUID4
