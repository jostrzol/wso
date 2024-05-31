from __future__ import annotations
from datetime import timedelta

from pydantic import UUID4, BaseModel, IPvAnyAddress


class Config(BaseModel):
    general: GeneralSettings
    managers: list[ManagerConfig]
    services: list[ServiceConfig]


class GeneralSettings(BaseModel):
    max_inactive: timedelta


class ManagerConfig(BaseModel):
    name: str
    address: IPvAnyAddress
    port: int = 8000
    token: UUID4
    imgs_path: str

    @property
    def host(self):
        return f"{self.address}:{self.port}"


class ServiceConfig(BaseModel):
    name: str
    image: str
    port: int
    replicas: int
