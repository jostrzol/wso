import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket
from pydantic import UUID4
from rich.console import Console
from rich.table import Table
from rich.live import Live

from .config import Config
from .manager import Manager
from .settings import settings


CONFIG = Config.model_validate(
    {
        "managers": [
            {
                "name": "host1",
                "address": "127.0.0.1",
                "token": "22a119cf-0bf3-4fb0-8c13-bd452a03432d",
            }
        ],
        "services": [
            {
                "name": "timesrv",
                "image": "alpine-virt-3.18.6-x86_64.iso",
                "port": 8080,
            }
        ],
        "vms": [
            {
                "service": "timesrv",
                "manager": "host1",
                "address": "127.0.0.1",
                "token": "f6f545eb-fa1b-489e-9c32-5b9260c59255",
            }
        ],
    }
)

FPS = 10

manager = Manager(name=settings.manager_name, config=CONFIG)


async def print_status():
    console = Console()
    with Live(console=console, screen=False, auto_refresh=False) as live:
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
            live.update(table, refresh=True)


def format_beat_delta(delta: timedelta) -> str:
    rounded = f"{delta.total_seconds() * 1000:.0f} ms"
    return (
        f"[red]{rounded}[/]"
        if delta > manager.config.general.max_inactive
        else f"[green]{rounded}[/]"
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(print_status())
    yield


app = FastAPI(lifespan=lifespan)


@app.websocket("/heartbeats/{token}")
async def websocket_endpoint(token: UUID4, websocket: WebSocket):
    await websocket.accept()
    while True:
        _ = await websocket.receive_text()
        manager.hearbeat(token)
