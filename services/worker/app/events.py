import json
from datetime import UTC, datetime
from uuid import uuid4

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue

from app.config import INSTANCE

EXCHANGE = "shop.events"
DEAD_LETTER_EXCHANGE = "shop.dlx"
DEAD_LETTER_QUEUE = "shop.dlq"
ORDERS_QUEUE = "worker.orders"
AUDIT_QUEUE = "worker.audit"
DELIVERY_LIMIT = 10
QUEUE_ARGUMENTS = {
    "x-queue-type": "quorum",
    "x-delivery-limit": DELIVERY_LIMIT,
    "x-dead-letter-exchange": DEAD_LETTER_EXCHANGE,
}


async def declare_topology(channel: AbstractChannel) -> tuple[AbstractQueue, AbstractQueue]:
    exchange = await channel.declare_exchange(EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True)
    dead_letters = await channel.declare_exchange(DEAD_LETTER_EXCHANGE, aio_pika.ExchangeType.FANOUT, durable=True)
    dead_letter_queue = await channel.declare_queue(
        DEAD_LETTER_QUEUE, durable=True, arguments={"x-queue-type": "quorum"}
    )
    await dead_letter_queue.bind(dead_letters)
    orders = await channel.declare_queue(ORDERS_QUEUE, durable=True, arguments=QUEUE_ARGUMENTS)
    await orders.bind(exchange, routing_key="order.created")
    audit = await channel.declare_queue(AUDIT_QUEUE, durable=True, arguments=QUEUE_ARGUMENTS)
    await audit.bind(exchange, routing_key="#")
    return orders, audit


async def publish(exchange: AbstractExchange, event: str, data: dict) -> None:
    envelope = {
        "id": str(uuid4()),
        "event": event,
        "at": datetime.now(UTC).isoformat(),
        "source": f"worker@{INSTANCE}",
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
    await exchange.publish(message, routing_key=event, timeout=5)
