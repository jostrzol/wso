import asyncio
from dataclasses import dataclass
from typing import AsyncGenerator, NoReturn


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def is_success(self) -> bool:
        return self.returncode == 0


async def run(
    cmd: str | list[str], env: dict | None = None, check: bool = True
) -> RunResult:

    match cmd:
        case str():
            joined_cmd = cmd
        case _:
            joined_cmd = " ".join(f"'{x}'" for x in cmd)

    proc = await asyncio.create_subprocess_shell(
        joined_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout, stderr = map(bytes.decode, await proc.communicate())

    if check and proc.returncode != 0:
        raise Exception(
            f"[{joined_cmd!r} exited with {proc.returncode}]\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )

    assert proc.returncode is not None
    return RunResult(returncode=proc.returncode, stdout=stdout, stderr=stderr)


async def run_untill_success(
    cmd: str | list[str],
    *,
    timeout: float = 30.0,
    interval: float = 3.0,
    env: dict | None = None,
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
