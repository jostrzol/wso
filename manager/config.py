from __future__ import annotations
from datetime import timedelta

from pydantic import UUID4, BaseModel, IPvAnyAddress


class Config(BaseModel):
    version: int
    general: GeneralSettings
    managers: list[ManagerConfig]
    vms: list[VMConfig]
    services: list[ServiceConfig]


class GeneralSettings(BaseModel):
    max_inactive: timedelta


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
    manager: str
    address: IPvAnyAddress
    token: UUID4
