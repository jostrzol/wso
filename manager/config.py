from __future__ import annotations
from datetime import timedelta
from functools import cached_property
from ipaddress import IPv4Address
from typing import Any, Iterable

from pydantic import BaseModel, UUID4, RootModel, model_validator, root_validator


class Config(BaseModel):
    general: GeneralSettings
    managers: list[ManagerConfig]
    services: list[ServiceConfig]


class GeneralSettings(BaseModel):
    max_inactive: timedelta


class ManagerConfig(BaseModel):
    name: str
    address: IPv4Address
    address_pool: IPv4Range
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


class IPv4Range(RootModel[str]):

    @model_validator(mode="before")
    @classmethod
    def prase_start_end(cls, data: Any):
        _, _ = map(IPv4Address, data.split("-"))
        return data

    @property
    def start(self) -> IPv4Address:
        start, _ = self.start_end
        return start

    @property
    def end(self) -> IPv4Address:
        _, end = self.start_end
        return end

    @cached_property
    def start_end(self) -> tuple[IPv4Address, IPv4Address]:
        start, end = map(IPv4Address, self.root.split("-"))
        return start, end

    def range(self) -> Iterable[IPv4Address]:
        start = int.from_bytes(self.start.packed)
        end = int.from_bytes(self.end.packed)
        for i in range(start, end + 1):
            yield IPv4Address(i)

    def generate_one_not_in(self, ips: Iterable[IPv4Address]) -> IPv4Address:
        forbidden = set(ips)
        for ip in self.range():
            if ip not in forbidden:
                return ip
        raise Exception("all ips taken")
