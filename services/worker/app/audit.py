import asyncio
import json
import logging

from aio_pika.abc import AbstractIncomingMessage
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.config import INSTANCE

log = logging.getLogger("worker.audit")

RETRY_DELAY = 3


class AuditLogger:
    def __init__(self, pool: AsyncConnectionPool) -> None:
        self.pool = pool

    async def handle(self, message: AbstractIncomingMessage) -> None:
        try:
            payload = json.loads(message.body)
            if not isinstance(payload, dict) or not payload.get("id"):
                raise ValueError("event id is missing")
        except ValueError as exc:
            log.error("message %s is malformed, sent to dead letter queue: %s", message.message_id, exc)
            await message.reject(requeue=False)
            return
        event_type = payload.get("event") or message.routing_key or "unknown"
        try:
            async with self.pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO event_log (event_type, payload, consumer) VALUES (%s, %s, %s) "
                    "ON CONFLICT ((payload ->> 'id')) DO NOTHING",
                    (event_type, Jsonb(payload), f"audit@{INSTANCE}"),
                )
        except Exception as exc:
            log.warning("event %s not logged, retry in %s s: %s", payload["id"], RETRY_DELAY, exc)
            await asyncio.sleep(RETRY_DELAY)
            await message.nack(requeue=True)
            return
        await message.ack()
        log.info("audit %s %s", event_type, payload["id"])
