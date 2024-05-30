from datetime import datetime
from typing import Generator, NoReturn
import subprocess
import libvirt

from pydantic import UUID4, IPvAnyAddress

from .utils import generate_timesrv_xml, IMGS_PATH
from .config import Config, ManagerConfig, VMConfig


class Manager:
    def __init__(self, name: str, config: Config):
        self._name = name
        self._config = config
        self._conn = libvirt.open('qemu:///system')

        tokens = self._relevant_tokens()
        now = datetime.now()
        self._last_hearbeats = {token: now for token in tokens}

    @property
    def config(self):
        return self._config

    def _relevant_tokens(self) -> Generator[UUID4, NoReturn, None]:
        yield from (
            *(manager.token for manager in self.other_managers()),
            *(vm.token for vm in self.my_vms()),
        )

    def other_managers(self) -> Generator[ManagerConfig, NoReturn, None]:
        yield from (
            manager for manager in self._config.managers if manager.name != self._name
        )

    def my_vms(self) -> Generator[VMConfig, NoReturn, None]:
        yield from (vm for vm in self._config.vms if vm.manager == self._name)

    def hearbeat(self, token: UUID4):
        self._last_hearbeats[token] = datetime.now()

    def last_heartbeat(self, token: UUID4) -> datetime:
        return self._last_hearbeats[token]

    def get_ip(self, domain_name: str):
        domain = self._conn.lookupByName(domain_name)
        ifaces = domain.interfaceAddresses(libvirt.VIR_DOMAIN_INTERFACE_ADDRESSES_SRC_AGENT, 0)

        return ifaces['eth0']['addrs'][0]['addr']

    def _create_timesrv_vm(self, name: str):
        subprocess.run(["cp", f"{IMGS_PATH}/timesrv.qcow2" , f"{IMGS_PATH}/{name}.qcow2"], check=True)

        try:
            self._conn.createXML(generate_timesrv_xml(name), 0)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to create VM: {e}")

    def delete_vm(self, name: str):
        try:
            dom = self._conn.lookupByName(name)

            if dom.isActive():
                dom.destroy()

            subprocess.run(["rm", f"{IMGS_PATH}/{name}.qcow2"], check=True)
        except libvirt.libvirtError as e:
            raise Exception(f"Failed to delete VM: {e}")

    def create_new_vm(self, name: str, service: str):
        if service == "timesrv":
            self._create_timesrv_vm(name)