import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket
from pydantic import UUID4
from rich.console import Console
from rich.table import Table

from .manager import Manager, manager
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
        table.add_column("Last beat before")
        now = datetime.now()
        for mgr in manager.other_managers():
            last_heartbeat = manager.last_heartbeat(mgr.token)
            delta = format_beat_delta(now - last_heartbeat)
            table.add_row("Manager", mgr.name, str(mgr.token), delta)
        for vm in manager.my_vms():
            last_heartbeat = manager.last_heartbeat(vm.token)
            delta = format_beat_delta(now - last_heartbeat)
            table.add_row("VM", None, str(vm.token), delta)
        console.print(table)


def format_beat_delta(delta: timedelta) -> str:
    rounded = f"{delta.total_seconds() * 1000:.0f} ms"
    return (
        f"[red]{rounded}[/]"
        if delta > manager.config.general.max_inactive
        else f"[green]{rounded}[/]"
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    global manager
    manager = await Manager.create(name=settings.manager_name)
    asyncio.create_task(print_status())
    yield


app = FastAPI(lifespan=lifespan)


@app.websocket("/heartbeats/{token}")
async def websocket_endpoint(token: UUID4, websocket: WebSocket):
    await websocket.accept()
    while True:
        _ = await websocket.receive_text()
        manager.hearbeat(token)
