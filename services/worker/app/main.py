import asyncio
import logging
import os
import signal
import time
from pathlib import Path

import aio_pika
from aio_pika.abc import AbstractRobustConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.audit import AuditLogger
from app.config import INSTANCE, secret
from app.events import AUDIT_QUEUE, EXCHANGE, ORDERS_QUEUE, declare_topology
from app.orders import OrderProcessor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")

HEARTBEAT_FILE = Path("/tmp/heartbeat")
HEARTBEAT_SECONDS = 10
PREFETCH = 10


def pg_conninfo() -> str:
    return make_conninfo(
        host=os.environ["PG_HOST"],
        port="5432",
        dbname=os.environ["PG_DB"],
        user=os.environ["PG_USER"],
        password=secret("PG_PASSWORD"),
        target_session_attrs="read-write",
        connect_timeout="2",
        keepalives_idle="5",
        tcp_user_timeout="5000",
        application_name=f"worker@{INSTANCE}",
    )


async def start_consuming(
    stop: asyncio.Event, pool: AsyncConnectionPool, redis: Redis
) -> AbstractRobustConnection | None:
    while not stop.is_set():
        connection = None
        try:
            connection = await aio_pika.connect_robust(
                host=os.environ["AMQP_HOST"],
                login=os.environ["AMQP_USER"],
                password=secret("AMQP_PASSWORD"),
                timeout=5,
            )
            publish_channel = await connection.channel(publisher_confirms=True)
            exchange = await publish_channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
            consume_channel = await connection.channel()
            await consume_channel.set_qos(prefetch_count=PREFETCH)
            orders_queue, audit_queue = await declare_topology(consume_channel)
            await orders_queue.consume(OrderProcessor(pool, redis, exchange).handle)
            await audit_queue.consume(AuditLogger(pool).handle)
            log.info("consuming %s and %s with prefetch %s", ORDERS_QUEUE, AUDIT_QUEUE, PREFETCH)
            return connection
        except Exception as exc:
            log.warning("rabbitmq unavailable: %s", exc)
            if connection is not None:
                await connection.close()
            await asyncio.sleep(2)
    return None


async def heartbeat(redis: Redis, stop: asyncio.Event) -> None:
    key = f"worker:heartbeat:{INSTANCE}"
    while not stop.is_set():
        HEARTBEAT_FILE.touch()
        try:
            await redis.set(key, f"{time.time():.3f}", ex=HEARTBEAT_SECONDS * 3)
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
    pool = AsyncConnectionPool(
        pg_conninfo(),
        min_size=1,
        max_size=5,
        timeout=10,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await pool.open(wait=False)
    log.info("worker %s started", INSTANCE)
    beat = asyncio.create_task(heartbeat(redis, stop))
    connection = await start_consuming(stop, pool, redis)
    await stop.wait()
    if connection is not None:
        await connection.close()
    await beat
    await pool.close()
    await redis.aclose()
    log.info("worker %s stopped", INSTANCE)


if __name__ == "__main__":
    asyncio.run(main())
