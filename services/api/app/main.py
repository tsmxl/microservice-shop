import asyncio
import logging
import os
import socket
import time
from contextlib import asynccontextmanager

import aio_pika
from fastapi import FastAPI, Request
from psycopg.conninfo import make_conninfo
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")

INSTANCE = socket.gethostname()


def secret(name: str) -> str:
    with open(os.environ[f"{name}_FILE"], encoding="utf-8") as f:
        return f.read().strip()


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
        application_name=f"api@{INSTANCE}",
    )


async def connect_amqp(app: FastAPI) -> None:
    while True:
        try:
            app.state.amqp = await aio_pika.connect_robust(
                host=os.environ["AMQP_HOST"],
                login=os.environ["AMQP_USER"],
                password=secret("AMQP_PASSWORD"),
                timeout=5,
            )
            log.info("connected to rabbitmq")
            return
        except Exception as exc:
            log.warning("rabbitmq unavailable: %s", exc)
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pg = AsyncConnectionPool(pg_conninfo(), min_size=1, max_size=10, timeout=3, open=False)
    await app.state.pg.open(wait=False)
    app.state.redis = Redis(
        host=os.environ["REDIS_HOST"],
        password=secret("REDIS_PASSWORD"),
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    app.state.amqp = None
    amqp_task = asyncio.create_task(connect_amqp(app))
    log.info("api %s started", INSTANCE)
    yield
    amqp_task.cancel()
    if app.state.amqp is not None:
        await app.state.amqp.close()
    await app.state.redis.aclose()
    await app.state.pg.close()


app = FastAPI(title="shop-api", lifespan=lifespan)


async def probe(check) -> str:
    try:
        await asyncio.wait_for(check(), timeout=3)
        return "ok"
    except Exception as exc:
        return f"error: {type(exc).__name__}"


async def check_postgres(request: Request) -> None:
    async with request.app.state.pg.connection(timeout=2) as conn:
        await conn.execute("SELECT 1")


async def check_redis(request: Request) -> None:
    await request.app.state.redis.ping()


async def check_rabbitmq(request: Request) -> None:
    connection = request.app.state.amqp
    if connection is None:
        raise ConnectionError("not connected yet")
    channel = await connection.channel()
    await channel.close()


async def alive_workers(redis: Redis) -> list[dict]:
    now = time.time()
    workers = []
    async for key in redis.scan_iter(match="worker:heartbeat:*"):
        value = await redis.get(key)
        if value is not None:
            workers.append({"instance": key.split(":", 2)[2], "seconds_ago": round(now - float(value), 1)})
    return sorted(workers, key=lambda worker: worker["instance"])


@app.get("/hello")
async def hello(request: Request) -> dict:
    try:
        workers = await alive_workers(request.app.state.redis)
    except Exception as exc:
        log.warning("cannot read worker heartbeats: %s", exc)
        workers = []
    return {"message": "Привет от api!", "instance": INSTANCE, "workers": workers}


@app.get("/health")
async def health(request: Request) -> dict:
    checks = {
        "postgres": await probe(lambda: check_postgres(request)),
        "redis": await probe(lambda: check_redis(request)),
        "rabbitmq": await probe(lambda: check_rabbitmq(request)),
    }
    status = "ok" if all(state == "ok" for state in checks.values()) else "degraded"
    return {"status": status, "instance": INSTANCE, "checks": checks}
