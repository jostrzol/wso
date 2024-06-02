from __future__ import annotations
import asyncio
from ipaddress import IPv4Address
import logging
import os
from pathlib import Path
import re
from typing import Iterable
from uuid import UUID

import libvirt

from manager.config import LBConfig, ManagerConfig, ServiceConfig
from manager.plan import LoadBalancer, Vm, Worker
from manager.utils import generate_nginx_conf, vm_conf

from .aiorun import poll, run, run_untill_success

logger = logging.getLogger("uvicorn")


class VMManager:
    def __init__(self, config: ManagerConfig):
        self._conn = libvirt.open("qemu:///system")
        self.config = config

    @property
    def imgs_path(self) -> Path:
        return Path(self.config.imgs_path)

    @property
    def name(self) -> str:
        return self.config.name

    VM_NAME_REGEX = re.compile(
        r"^wso-(.*)-(.*)-(.*)-"
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
    )

    def list_current_vms(self) -> Iterable[Vm]:
        for domain in self._conn.listAllDomains():
            match = re.match(self.VM_NAME_REGEX, domain.name())
            if match is None:
                continue
            manager, type, service, token = match.groups()
            if manager != self.name:
                continue
            stubs = {
                "address": IPv4Address("127.0.0.1"),
                "port": 0,
            }
            match type:
                case "wrk":
                    yield Worker(
                        service=service, manager=manager, token=UUID(token), **stubs
                    )
                case "lb":
                    yield LoadBalancer(
                        service=service,
                        manager=manager,
                        token=UUID(token),
                        upstream=[],
                        **stubs,
                    )

    async def create_new_vm(self, vm: Vm, config: ServiceConfig | LBConfig):
        await self._make_new_vm_image(vm, config)
        await self._make_new_vm(vm)
        await self._wait_for_network_and_setup_ip(vm)

        match vm:
            case LoadBalancer() as lb:
                await self.setup_nginx(lb)
            case Worker(service="timesrv") as wrk:
                await self._setup_timesrv(wrk)

    async def _make_new_vm_image(self, vm: Vm, service: ServiceConfig | LBConfig):
        src_path = self.imgs_path / service.image
        dst_path = (self.imgs_path / vm.name).with_suffix(".qcow2")
        await run(f'cp "{src_path}" "{dst_path}"')

    async def _make_new_vm(self, vm: Vm):
        try:
            xml = vm_conf(self.imgs_path, vm.name)
            await self._create_xml(xml)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to create VM: {e}")
        await self._wait_until_fully_booted(vm)

    async def _wait_until_fully_booted(self, vm: Vm):
        async for _ in poll(timeout=120, interval=5):
            if await self._is_fully_booted(vm.name):
                return
        raise Exception("unreachable")

    async def _is_fully_booted(self, name: str):
        try:
            dom = await self._lookup_by_name(name)
            if not dom.isActive():
                return False
            result = await run(
                ["virsh", "qemu-agent-command", name, '{"execute": "guest-ping"}'],
                check=False,
            )
            return "return" in result.stdout
        except libvirt.libvirtError:
            return False

    async def _wait_for_network_and_setup_ip(self, vm: Vm):
        curr_ip = await self.get_ip_until_success(vm.name)
        await self._setup_ip(vm, curr_ip)
        await self._wait_ip_reachable(vm.address)

    async def get_ip_until_success(self, domain_name: str) -> IPv4Address:
        async for _ in poll():
            ip = await self.get_ip(domain_name)
            if ip is not None:
                return ip
        raise Exception("unreachable")

    async def get_ip(self, domain_name: str) -> IPv4Address | None:
        def impl():
            domain = self._conn.lookupByName(domain_name)
            return domain.interfaceAddresses(
                libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT, 0
            )

        ifaces = await asyncio.to_thread(impl)

        try:
            return IPv4Address(ifaces["eth0"]["addrs"][0]["addr"])
        except Exception:
            return None

    async def _setup_ip(self, vm: Vm, curr_ip: IPv4Address):
        await self._ansible(
            playbook="setup_network/playbook.yaml",
            host=curr_ip,
            variables={"curr_ip": curr_ip, "new_ip": vm.address},
        )

    async def _wait_ip_reachable(self, ip: IPv4Address):
        await run_untill_success(f"ping -c 1 {ip}", timeout=20)

    async def setup_nginx(self, lb: LoadBalancer):
        upstream = [f"{ip}:{port}" for ip, port in lb.upstream]

        generate_nginx_conf(upstream, self.imgs_path, port=lb.port)
        await self._ansible(
            playbook="setup_nginx/playbook.yaml",
            host=lb.address,
            variables={"ip": lb.address},
        )

    async def _setup_timesrv(self, vm: Vm):
        await self._ansible(
            playbook="run_timesrv/playbook.yaml",
            host=vm.address,
            variables={
                "ip": vm.address,
                "app_port": vm.port,
                "wsotimesrv_token": vm.token,
                "wsotimesrv_manager_address": self.config.host,
            },
        )

    async def delete_vm(self, name: str):
        try:
            dom = await self._lookup_by_name(name)
            if dom.isActive():
                dom.destroy()
            await run(["rm", f"{self.imgs_path}/{name}.qcow2"])
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to delete VM: {e}")

    async def _ansible(self, playbook: str, host: str | IPv4Address, variables: dict):
        variables_str = " ".join(f"{key}={val}" for key, val in variables.items())
        result = await run(
            [
                "ansible-playbook",
                "-i",
                f"{host},",
                f"{self.imgs_path}/../ansible/{playbook}",
                "-e",
                variables_str,
            ],
            env={**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False"},
        )
        logger.info("run ansible")
        print("STDOUT", result.stdout, "STDERR", result.stderr)
        return result

    async def _create_xml(self, xml: str):
        return await asyncio.to_thread(self._conn.createXML, xml)

    async def _lookup_by_name(self, name: str):
        return await asyncio.to_thread(self._conn.lookupByName, name)
