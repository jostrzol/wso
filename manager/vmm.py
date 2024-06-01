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

    async def wait_until_fully_booted(self, name: str, timeout: int = 300):
        start = time()
        while time() - start < timeout:
            if self.is_fully_booted(name):
                return True
            await asyncio.sleep(5)
        raise Exception("Timed out waiting for VM to fully boot")

    async def wait_and_setup_ip(self, name: str, ip: IPv4Address):
        await self.wait_until_fully_booted(name)
        self._setup_ip(name, ip)

    async def _create_timesrv_vm(self, ip: IPv4Address, name: str):
        def impl():
            subprocess.run(
                [
                    "cp",
                    f"{self.imgs_path}/timesrv.qcow2",
                    f"{self.imgs_path}/{name}.qcow2",
                ],
                check=True,
            )

            try:
                self._conn.createXML(generate_timesrv_xml(self.imgs_path, name), 0)
            except libvirt.libvirtError as e:
                raise Exception(f"Failed to create VM: {e}")

            self._conn.lookupByName(name)

        await asyncio.to_thread(impl)
        await self.wait_and_setup_ip(name, ip)

    def _setup_ip(self, name: str, ip: IPv4Address):
        curr_ip = self.get_ip(name)

        # TODO: jo: możesz pozmieniać args jak zmieniłeś tego managera i ipki
        token = "todo"

        res = subprocess.run(
            [
                "ansible-playbook",
                "-i",
                f"{ip},",
                f"{self.imgs_path}/../ansible/run_timesrv/playbook.yaml",
                "-e",
                f"ip={ip} wsotimesrv_token={token}",
            ],
            env={**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False"},
            check=True,
        )

        if res.returncode != 0:
            raise Exception(f"Failed to run timesrv on vm: {name}")

    def _start_timesrv(self, name: str):
        curr_ip = self.get_ip(name)

        res = subprocess.run(
            [
                "ansible-playbook",
                "-i",
                f"{curr_ip},",
                f"{self.imgs_path}/../ansible/setup_network/playbook.yaml",
                "-e",
                f"curr_ip={curr_ip} new_ip={new_ip}",
            ],
            env={**os.environ, "ANSIBLE_HOST_KEY_CHECKING": "False"},
            check=True,
        )

        if res.returncode != 0:
            raise Exception("Failed to setup IP")

    def delete_vm(self, name: str):
        try:
            dom = self._conn.lookupByName(name)

            if dom.isActive():
                dom.destroy()

            subprocess.run(["rm", f"{self.imgs_path}/{name}.qcow2"], check=True)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to delete VM: {e}")

    async def create_new_vm(self, name: str, ip: IPv4Address, service: str):
        if service == "timesrv":
            await self._create_timesrv_vm(ip, name)
