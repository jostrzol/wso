import asyncio
from ipaddress import IPv4Address
import os
import re
import subprocess
from time import time
from typing import Iterable
import libvirt
from pydantic import UUID4

from manager.config import ManagerConfig
from manager.plan import VMConfig
from manager.utils import generate_timesrv_xml


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
                token=UUID4(token),
            )

    def get_ip(self, domain_name: str):
        domain = self._conn.lookupByName(domain_name)
        ifaces = domain.interfaceAddresses(
            libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT, 0
        )

        return ifaces["eth0"]["addrs"][0]["addr"]

    def is_fully_booted(self, name: str):
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

    async def wait_until_fully_booted(self, vm: VMConfig, timeout: int = 300):
        start = time()
        while time() - start < timeout:
            if self.is_fully_booted(vm.name):
                return True
            await asyncio.sleep(5)
        raise Exception("Timed out waiting for VM to fully boot")

    async def wait_and_setup_ip(self, vm: VMConfig):
        await self.wait_until_fully_booted(vm)
        await self._setup_ip(vm)

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

    async def _setup_ip(self, vm: VMConfig):
        def impl():
            curr_ip = self.get_ip(vm.name)

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

    async def _start_timesrv(self, vm: VMConfig):
        def impl():
            res = subprocess.run(
                [
                    "ansible-playbook",
                    "-i",
                    f"{vm.address},",
                    f"{self.imgs_path}/../ansible/run_timesrv/playbook.yaml",
                    "-e",
                    f"ip={self.config.host} wsotimesrv_token={vm.token}",
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
