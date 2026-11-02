import asyncio
import json
import logging
from datetime import UTC, datetime

from aio_pika.abc import AbstractExchange, AbstractIncomingMessage
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import INSTANCE
from app.events import publish

log = logging.getLogger("worker.orders")

FEED_KEY = "feed:recent"
FEED_SIZE = 20
LIST_KEY = "products:all"
RETRY_DELAY = 3


def mask(customer: str) -> str:
    return f"{customer[0]}•••"


class OrderProcessor:
    def __init__(self, pool: AsyncConnectionPool, redis: Redis, exchange: AbstractExchange) -> None:
        self.pool = pool
        self.redis = redis
        self.exchange = exchange

    async def handle(self, message: AbstractIncomingMessage) -> None:
        try:
            event = json.loads(message.body)
            order_id = int(event["data"]["order_id"])
        except (ValueError, KeyError, TypeError) as exc:
            log.error("message %s is malformed, sent to dead letter queue: %s", message.message_id, exc)
            await message.reject(requeue=False)
            return
        try:
            result = await self.process(order_id)
        except Exception as exc:
            log.warning("order %s failed, retry in %s s: %s", order_id, RETRY_DELAY, exc)
            await asyncio.sleep(RETRY_DELAY)
            await message.nack(requeue=True)
            return
        if result is None:
            log.info("order %s is already processed, duplicate delivery skipped", order_id)
        else:
            await self.after_commit(result)
            log.info("order %s %s by %s: %s", order_id, result["status"], INSTANCE, result["reason"] or "ok")
        await message.ack()

    async def process(self, order_id: int) -> dict | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "SELECT id, customer, status, total FROM orders WHERE id = %s FOR UPDATE",
                (order_id,),
            )
            order = await cur.fetchone()
            if order is None or order["status"] != "pending":
                return None
            cur = await conn.execute(
                "SELECT oi.product_id, oi.qty, p.title, p.artist, p.format, p.stock, p.per_customer_limit, "
                "p.drop_at <= now() AS started "
                "FROM order_items oi JOIN products p ON p.id = oi.product_id "
                "WHERE oi.order_id = %s ORDER BY oi.product_id FOR UPDATE OF p",
                (order_id,),
            )
            items = await cur.fetchall()
            reason = await self.check(conn, order["customer"], items)
            if reason is None:
                for item in items:
                    await conn.execute(
                        "UPDATE products SET stock = stock - %s, updated_at = now() WHERE id = %s",
                        (item["qty"], item["product_id"]),
                    )
            status = "confirmed" if reason is None else "rejected"
            await conn.execute(
                "UPDATE orders SET status = %s, reason = %s, processed_at = now(), processed_by = %s "
                "WHERE id = %s AND status = 'pending'",
                (status, reason, INSTANCE, order_id),
            )
        return {
            "order_id": order_id,
            "customer": order["customer"],
            "status": status,
            "reason": reason,
            "total": str(order["total"]),
            "items": items,
        }

    async def check(self, conn: AsyncConnection, customer: str, items: list[dict]) -> str | None:
        if not items:
            return "В заказе нет позиций"
        for item in items:
            title = f"«{item['title']}»"
            if not item["started"]:
                return f"Продажи {title} ещё не начались"
            cur = await conn.execute(
                "SELECT coalesce(sum(oi.qty), 0) AS bought "
                "FROM order_items oi JOIN orders o ON o.id = oi.order_id "
                "WHERE o.customer = %s AND o.status = 'confirmed' AND oi.product_id = %s",
                (customer, item["product_id"]),
            )
            bought = (await cur.fetchone())["bought"]
            limit = item["per_customer_limit"]
            if bought + item["qty"] > limit:
                return f"{title}: не больше {limit} шт. в одни руки, у вас уже {bought}"
            if item["stock"] < item["qty"]:
                if item["stock"] == 0:
                    return f"{title} распродан"
                return f"{title}: осталось только {item['stock']} шт."
        return None

    async def after_commit(self, result: dict) -> None:
        if result["status"] == "confirmed":
            now = datetime.now(UTC).isoformat()
            keys = [f"product:{item['product_id']}" for item in result["items"]]
            entries = [
                json.dumps(
                    {
                        "order_id": result["order_id"],
                        "title": item["title"],
                        "artist": item["artist"],
                        "format": item["format"],
                        "qty": item["qty"],
                        "customer": mask(result["customer"]),
                        "at": now,
                    },
                    ensure_ascii=False,
                )
                for item in result["items"]
            ]
            try:
                await self.redis.delete(*keys, LIST_KEY)
                await self.redis.lpush(FEED_KEY, *entries)
                await self.redis.ltrim(FEED_KEY, 0, FEED_SIZE - 1)
            except (RedisError, OSError) as exc:
                log.warning("order %s: cache and feed not updated: %s", result["order_id"], exc)
        data = {
            "order_id": result["order_id"],
            "customer": result["customer"],
            "status": result["status"],
            "reason": result["reason"],
            "total": result["total"],
            "processed_by": INSTANCE,
        }
        try:
            await publish(self.exchange, f"order.{result['status']}", data)
        except Exception as exc:
            log.warning("order %s: event order.%s not published: %s", result["order_id"], result["status"], exc)
