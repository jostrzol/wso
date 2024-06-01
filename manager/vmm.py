from __future__ import annotations
import asyncio
from dataclasses import dataclass
from ipaddress import IPv4Address
import logging
import os
import re
import subprocess
from typing import AsyncGenerator, Iterable, NoReturn
from uuid import UUID

import libvirt

from manager.config import ManagerConfig
from manager.plan import VMConfig
from manager.utils import generate_nginx_conf, generate_timesrv_xml

logger = logging.getLogger("uvicorn")


class VMManager:
    def __init__(self, config: ManagerConfig):
        self._conn = libvirt.open("qemu:///system")
        self.config = config

    @property
    def imgs_path(self) -> str:
        return self.config.imgs_path

    @property
    def name(self) -> str:
        return self.config.name

    VM_NAME_REGEX = re.compile(
        r"^wso-(.*)-(.*)-"
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$"
    )

    def list_current_vms(self) -> Iterable[VMConfig]:
        for domain in self._conn.listAllDomains():
            match = re.match(self.VM_NAME_REGEX, domain.name())
            if match is None:
                continue
            service, manager, token = match.groups()
            if manager != self.name:
                continue
            yield VMConfig(
                service=service,
                manager=manager,
                address=IPv4Address("127.0.0.1"),
                port=0,
                token=UUID(token),
            )

    async def wait_and_setup_ip(self, vm: VMConfig):
        await self._wait_until_fully_booted(vm)
        curr_ip = await self.get_ip_until_success(vm.name)
        await self._setup_ip(vm, curr_ip)
        await self._wait_ip_reachable(vm.address)

    async def _wait_until_fully_booted(self, vm: VMConfig):
        async for _ in poll():
            if await self._is_fully_booted(vm.name):
                return
        raise Exception("unreachable")

    async def _is_fully_booted(self, name: str):
        def impl():
            try:
                dom = self._conn.lookupByName(name)
                if dom.isActive():
                    result = subprocess.run(
                        [
                            "virsh",
                            "qemu-agent-command",
                            name,
                            '{"execute":"guest-ping"}',
                        ],
                        capture_output=True,
                        text=True,
                    )

                    if "return" in result.stdout:
                        return True
            except libvirt.libvirtError:
                pass

            return False

        return await asyncio.to_thread(impl)

    async def get_ip_until_success(
        self, domain_name: str
    ) -> IPv4Address:
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

    async def _wait_ip_reachable(self, ip: IPv4Address):
        await run_untill_success(f"ping -c 1 {ip}", timeout=20)

    async def _create_timesrv_vm(self, vm: VMConfig):
        def impl():
            subprocess.run(
                [
                    "cp",
                    f"{self.imgs_path}/timesrv.qcow2",
                    f"{self.imgs_path}/{vm.name}.qcow2",
                ],
                check=True,
            )

            try:
                self._conn.createXML(generate_timesrv_xml(self.imgs_path, vm.name), 0)
            except libvirt.libvirtError as e:
                raise Exception(f"Failed to create VM: {e}")

            self._conn.lookupByName(vm.name)

        await asyncio.to_thread(impl)
        await self.wait_and_setup_ip(vm)

    async def _create_nginx_vm(self, vm: VMConfig):
        def impl():
            subprocess.run(
                [
                    "cp",
                    f"{self.imgs_path}/nginx.qcow2",
                    f"{self.imgs_path}/{vm.name}.qcow2",
                ],
                check=True,
            )

            try:
                self._conn.createXML(generate_timesrv_xml(self.imgs_path, vm.name), 0)
            except libvirt.libvirtError as e:
                raise Exception(f"Failed to create VM: {e}")

            self._conn.lookupByName(vm.name)

        await asyncio.to_thread(impl)
        await self.wait_and_setup_ip(vm)
        await self._setup_nginx(vm)

    async def _setup_ip(self, vm: VMConfig, curr_ip: IPv4Address):
        def impl():
            res = subprocess.run(
                [
                    "ansible-playbook",
                    "-i",
                    f"{curr_ip},",
                    f"{self.imgs_path}/../ansible/setup_network/playbook.yaml",
                    "-e",
                    f"curr_ip={curr_ip} new_ip={vm.address}",
                ],
                env={**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False"},
                check=True,
            )

            if res.returncode != 0:
                raise Exception("Failed to setup IP")

        await asyncio.to_thread(impl)

    async def _setup_nginx(self, vm: VMConfig):
        server_ips = [
            vm.address for vm in self.list_current_vms() if vm.service == "timesrv"
        ]

        generate_nginx_conf(server_ips, self.imgs_path)

        def impl():
            res = subprocess.run(
                [
                    "ansible-playbook",
                    "-i",
                    f"{vm.address},",
                    f"{self.imgs_path}/../ansible/setup_nginx/playbook.yaml",
                    "-e",
                    f"ip={vm.address}",
                ],
                env={**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False"},
                check=True,
            )

            if res.returncode != 0:
                raise Exception("Failed to setup nginx")

        await asyncio.to_thread(impl)

    async def start_timesrv(self, vm: VMConfig):
        def impl():
            res = subprocess.run(
                [
                    "ansible-playbook",
                    "-i",
                    f"{vm.address},",
                    f"{self.imgs_path}/../ansible/run_timesrv/playbook.yaml",
                    "-e",
                    (
                        f"ip={vm.address} "
                        f"app_port={vm.port} "
                        f"wsotimesrv_token={vm.token} "
                        f"wsotimesrv_manager_address={self.config.host} "
                    ),
                ],
                env={**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False"},
                check=True,
            )

            if res.returncode != 0:
                raise Exception(f"Failed to run timesrv on vm: {vm.name}")

        await asyncio.to_thread(impl)

    def delete_vm(self, name: str):
        try:
            dom = self._conn.lookupByName(name)

            if dom.isActive():
                dom.destroy()

            subprocess.run(["rm", f"{self.imgs_path}/{name}.qcow2"], check=True)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to delete VM: {e}")

    async def create_new_vm(self, vm: VMConfig):
        if vm.service == "timesrv":
            await self._create_timesrv_vm(vm)
        if vm.service == "nginx":
            await self._create_nginx_vm(vm)


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def is_success(self) -> bool:
        return self.returncode == 0


async def run(cmd: str, env: dict | None = None, check: bool = True) -> RunResult:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env
    )

    stdout, stderr = await proc.communicate()

    if check and proc.returncode != 0:
        raise Exception(f"[{cmd!r} exited with {proc.returncode}]")

    assert proc.returncode is not None
    return RunResult(
        returncode=proc.returncode, stdout=stdout.decode(), stderr=stderr.decode()
    )


async def run_untill_success(
    cmd: str, *, timeout: float = 30.0, interval: float = 3.0, env: dict | None = None
) -> RunResult:
    async for _ in poll(timeout=timeout, interval=interval):
        result = await run(cmd=cmd, env=env, check=False)
        if result.is_success:
            return result
        await asyncio.sleep(interval)
    raise Exception("unreachable")


async def poll(
    timeout: float = 30.0, interval: float = 3.0
) -> AsyncGenerator[None, NoReturn]:
    async with asyncio.timeout(timeout):
        while True:
            yield
            await asyncio.sleep(interval)
