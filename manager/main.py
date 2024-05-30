from fastapi import FastAPI, WebSocket
from pydantic import UUID4

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

manager = Manager(name=settings.manager_name, config=CONFIG)


app = FastAPI()


@app.websocket("/heartbeats/{token}")
async def websocket_endpoint(token: UUID4, websocket: WebSocket):
    await websocket.accept()
    while True:
        _ = await websocket.receive_text()
        manager.hearbeat(token)
        print(manager._last_hearbeats)
