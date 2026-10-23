import asyncio
import logging
import os
import signal
import socket
import time
from pathlib import Path

import aio_pika
from aio_pika.abc import AbstractRobustConnection
from redis.asyncio import Redis
from redis.exceptions import RedisError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")

INSTANCE = socket.gethostname()
HEARTBEAT_FILE = Path("/tmp/heartbeat")
HEARTBEAT_SECONDS = 10


def secret(name: str) -> str:
    with open(os.environ[f"{name}_FILE"], encoding="utf-8") as f:
        return f.read().strip()


async def connect_amqp(stop: asyncio.Event) -> AbstractRobustConnection | None:
    while not stop.is_set():
        try:
            connection = await aio_pika.connect_robust(
                host=os.environ["AMQP_HOST"],
                login=os.environ["AMQP_USER"],
                password=secret("AMQP_PASSWORD"),
                timeout=5,
            )
            log.info("connected to rabbitmq")
            return connection
        except Exception as exc:
            log.warning("rabbitmq unavailable: %s", exc)
            await asyncio.sleep(2)
    return None


async def heartbeat(redis: Redis, stop: asyncio.Event) -> None:
    key = f"worker:heartbeat:{INSTANCE}"
    while not stop.is_set():
        HEARTBEAT_FILE.touch()
        try:
            await redis.set(key, f"{time.time():.3f}", ex=HEARTBEAT_SECONDS * 3)
            log.info("heartbeat")
        except RedisError as exc:
            log.warning("heartbeat not stored in redis: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
        except TimeoutError:
            pass


async def main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    redis = Redis(
        host=os.environ["REDIS_HOST"],
        password=secret("REDIS_PASSWORD"),
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    log.info("worker %s started", INSTANCE)
    beat = asyncio.create_task(heartbeat(redis, stop))
    connection = await connect_amqp(stop)
    await stop.wait()
    await beat
    if connection is not None:
        await connection.close()
    await redis.aclose()
    log.info("worker %s stopped", INSTANCE)


if __name__ == "__main__":
    asyncio.run(main())
