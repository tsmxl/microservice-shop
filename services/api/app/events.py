import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from uuid import uuid4

import aio_pika
from aio_pika.abc import AbstractExchange, AbstractRobustConnection

from app.config import INSTANCE, secret

log = logging.getLogger("api.events")

EXCHANGE = "shop.events"
PUBLISH_SECONDS = 3


class Events:
    def __init__(self) -> None:
        self.connection: AbstractRobustConnection | None = None
        self.exchange: AbstractExchange | None = None

    async def connect(self) -> None:
        while True:
            connection = None
            try:
                connection = await aio_pika.connect_robust(
                    host=os.environ["AMQP_HOST"],
                    login=os.environ["AMQP_USER"],
                    password=secret("AMQP_PASSWORD"),
                    timeout=5,
                )
                channel = await connection.channel(publisher_confirms=True)
                self.exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
                self.connection = connection
                log.info("connected to rabbitmq, exchange %s declared", EXCHANGE)
                return
            except Exception as exc:
                log.warning("rabbitmq unavailable: %s", exc)
                if connection is not None:
                    await connection.close()
                await asyncio.sleep(2)

    async def publish(self, event: str, data: dict) -> None:
        if self.exchange is None:
            raise ConnectionError("rabbitmq is not connected")
        envelope = {
            "id": str(uuid4()),
            "event": event,
            "at": datetime.now(UTC).isoformat(),
            "source": f"api@{INSTANCE}",
            "data": data,
        }
        message = aio_pika.Message(
            json.dumps(envelope, ensure_ascii=False).encode(),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            message_id=envelope["id"],
            type=event,
            app_id=envelope["source"],
        )
        await self.exchange.publish(message, routing_key=event, timeout=PUBLISH_SECONDS)

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
