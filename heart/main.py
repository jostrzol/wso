import asyncio
from heart.heart import Heart

from heart.settings import settings


if __name__ == "__main__":
    heart = Heart(manager_address=settings.manager_address, token=settings.token)
    asyncio.run(heart.beat_forever())
