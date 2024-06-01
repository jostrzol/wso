import asyncio
from contextlib import asynccontextmanager
from ipaddress import IPv4Address

from fastapi import FastAPI, WebSocket
from pydantic import UUID4
from rich.console import Console
from rich.table import Table

from .manager import ConnectionStatus, Manager, manager
from .settings import settings

FPS = 1


async def print_status():
    console = Console()
    while True:
        await asyncio.sleep(1 / FPS)
        table = Table(show_footer=False)
        table.add_column("Type")
        table.add_column("Name")
        table.add_column("Address")
        table.add_column("Last beat before", justify="right")
        for mgr, status in manager.manager_statuses():
            delta = format_last_beat(status)
            table.add_row("Manager", mgr.name, mgr.host, delta)
        for vm, status in manager.vm_statuses():
            delta = format_last_beat(status)
            table.add_row("VM", vm.name, vm.host, delta)
        console.print(table)


def format_last_beat(status: ConnectionStatus) -> str:
    if status.last_beat_before is None:
        return "-"
    rounded = f"{status.last_beat_before.total_seconds() * 1000:.0f} ms"
    return f"[red]{rounded}[/]" if status.is_dead else f"[green]{rounded}[/]"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global manager
    manager = await Manager.create(name=settings.manager_name)
    asyncio.create_task(print_status())
    asyncio.create_task(manager.background_loop())
    yield


app = FastAPI(lifespan=lifespan)


@app.websocket("/heartbeats/{token}")
async def websocket_endpoint(token: UUID4, websocket: WebSocket):
    await websocket.accept()
    while True:
        _ = await websocket.receive_text()
        if not manager.hearbeat(token):
            await websocket.close(code=1008, reason=f"Did not expect token '{token}'")
            break


@app.get("/create_time/{name}")
async def create_vm(name: str, ip: IPv4Address):
    await manager._vmm.create_new_vm(name, ip, "timesrv")


@app.get("/ip/{domain_name}")
async def get_ip(domain_name: str):
    return {"ip": await manager._vmm.get_ip(domain_name)}


@app.get("/delete/{name}")
def delete_vm(name: str):
    manager._vmm.delete_vm(name)
    return {"status": "ok"}
