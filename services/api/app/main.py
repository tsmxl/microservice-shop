import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from redis.asyncio import Redis

from app import admin, catalog, orders
from app.cache import Cache
from app.config import INSTANCE, secret
from app.events import Events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pg = AsyncConnectionPool(
        pg_conninfo(),
        min_size=1,
        max_size=10,
        timeout=3,
        kwargs={"row_factory": dict_row},
        open=False,
    )
    await app.state.pg.open(wait=False)
    app.state.redis = Redis(
        host=os.environ["REDIS_HOST"],
        password=secret("REDIS_PASSWORD"),
        decode_responses=True,
        socket_timeout=1,
        socket_connect_timeout=1,
    )
    app.state.cache = Cache(app.state.redis)
    app.state.events = Events()
    app.state.admin_password = secret("ADMIN_PASSWORD")
    app.state.admin_token = secret("ADMIN_TOKEN")
    connect_task = asyncio.create_task(app.state.events.connect())
    log.info("api %s started", INSTANCE)
    yield
    connect_task.cancel()
    await app.state.events.close()
    await app.state.redis.aclose()
    await app.state.pg.close()


app = FastAPI(title="shop-api", lifespan=lifespan)
app.include_router(catalog.router)
app.include_router(orders.router)
app.include_router(admin.router)


@app.middleware("http")
async def instance_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Instance"] = INSTANCE
    return response


@app.exception_handler(PoolTimeout)
@app.exception_handler(psycopg.OperationalError)
async def database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.warning("database unavailable on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse({"detail": "База данных недоступна"}, status_code=503)


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
    connection = request.app.state.events.connection
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
