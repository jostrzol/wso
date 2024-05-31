import asyncio
from contextlib import asynccontextmanager

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
        table.add_column("Token")
        table.add_column("Last beat before", justify="right")
        for mgr in manager.other_managers():
            status = manager.connection_status(mgr.token)
            delta = format_last_beat(status)
            table.add_row("Manager", mgr.name, str(mgr.token), delta)
        for vm in manager.my_vms():
            status = manager.connection_status(vm.token)
            delta = format_last_beat(status)
            table.add_row("VM", None, str(vm.token), delta)
        console.print(table)


def format_last_beat(status: ConnectionStatus) -> str:
    rounded = f"{status.last_beat_before.total_seconds() * 1000:.0f} ms"
    return f"[red]{rounded}[/]" if status.is_dead else f"[green]{rounded}[/]"


@asynccontextmanager
async def lifespan(_: FastAPI):
    global manager
    manager = await Manager.create(name=settings.manager_name)
    asyncio.create_task(print_status())
    asyncio.create_task(manager.watch_changes_forever())
    asyncio.create_task(manager.execute_plan_forever())
    yield


app = FastAPI(lifespan=lifespan)


@app.websocket("/heartbeats/{token}")
async def websocket_endpoint(token: UUID4, websocket: WebSocket):
    await websocket.accept()
    while True:
        _ = await websocket.receive_text()
        manager.hearbeat(token)


@app.get("/create_time/{name}")
async def create_vm(name: str):
    manager.create_new_vm(name, "timesrv")


@app.get("/ip/{domain_name}")
def get_ip(domain_name: str):
    return {"ip": manager.get_ip(domain_name)}


@app.get("/delete/{name}")
def delete_vm(name: str):
    manager.delete_vm(name)
    return {"status": "ok"}
