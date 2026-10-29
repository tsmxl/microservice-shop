import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.admin import require_admin
from app.config import INSTANCE

log = logging.getLogger("api.orders")
router = APIRouter()

BROKER_DOWN = "Сервис заказов временно недоступен"


class OrderItemIn(BaseModel):
    product_id: int = Field(gt=0)
    qty: int = Field(ge=1, le=10)


class OrderIn(BaseModel):
    customer: str = Field(min_length=1, max_length=100)
    items: list[OrderItemIn] = Field(min_length=1, max_length=10)


def iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def order_json(row: dict) -> dict:
    return {
        "id": row["id"],
        "status": row["status"],
        "reason": row["reason"],
        "total": str(row["total"]),
        "created_at": iso(row["created_at"]),
        "processed_at": iso(row["processed_at"]),
        "processed_by": row["processed_by"],
    }


@router.post("/orders", status_code=202)
async def create_order(payload: OrderIn, request: Request) -> JSONResponse:
    customer = payload.customer.strip()
    if not customer:
        raise HTTPException(422, "Укажите имя покупателя")
    quantities: dict[int, int] = {}
    for item in payload.items:
        quantities[item.product_id] = quantities.get(item.product_id, 0) + item.qty
    pg = request.app.state.pg
    async with pg.connection() as conn:
        cur = await conn.execute("SELECT id, price FROM products WHERE id = ANY(%s)", (list(quantities),))
        prices = {row["id"]: row["price"] for row in await cur.fetchall()}
        missing = [str(product_id) for product_id in quantities if product_id not in prices]
        if missing:
            raise HTTPException(404, f"Релиз не найден: {', '.join(missing)}")
        total = sum((prices[product_id] * qty for product_id, qty in quantities.items()), Decimal("0"))
        cur = await conn.execute(
            "INSERT INTO orders (customer, total) VALUES (%s, %s) RETURNING id",
            (customer, total),
        )
        order_id = (await cur.fetchone())["id"]
        async with conn.cursor() as items_cur:
            await items_cur.executemany(
                "INSERT INTO order_items (order_id, product_id, qty, price) VALUES (%s, %s, %s, %s)",
                [(order_id, product_id, qty, prices[product_id]) for product_id, qty in quantities.items()],
            )
    data = {
        "order_id": order_id,
        "customer": customer,
        "total": str(total),
        "items": [{"product_id": product_id, "qty": qty} for product_id, qty in quantities.items()],
    }
    try:
        await request.app.state.events.publish("order.created", data)
    except Exception as exc:
        log.error("order %s not published, rejecting: %s", order_id, exc)
        async with pg.connection() as conn:
            await conn.execute(
                "UPDATE orders SET status = 'rejected', reason = %s, processed_at = now(), processed_by = %s "
                "WHERE id = %s AND status = 'pending'",
                (BROKER_DOWN, f"api@{INSTANCE}", order_id),
            )
        raise HTTPException(503, f"{BROKER_DOWN}, попробуйте позже") from exc
    log.info("order %s accepted, published order.created", order_id)
    return JSONResponse(
        {"id": order_id, "status": "pending"},
        status_code=202,
        headers={"Location": f"/orders/{order_id}"},
    )


@router.get("/orders/{order_id}")
async def get_order(order_id: int, request: Request) -> dict:
    async with request.app.state.pg.connection() as conn:
        cur = await conn.execute(
            "SELECT id, status, reason, total, created_at, processed_at, processed_by FROM orders WHERE id = %s",
            (order_id,),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPException(404, "Заказ не найден")
        cur = await conn.execute(
            "SELECT oi.product_id, p.title, oi.qty, oi.price "
            "FROM order_items oi JOIN products p ON p.id = oi.product_id "
            "WHERE oi.order_id = %s ORDER BY oi.product_id",
            (order_id,),
        )
        items = await cur.fetchall()
    return {
        **order_json(row),
        "items": [
            {"product_id": item["product_id"], "title": item["title"], "qty": item["qty"], "price": str(item["price"])}
            for item in items
        ],
    }


@router.get("/orders", dependencies=[Depends(require_admin)])
async def list_orders(request: Request, limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
    async with request.app.state.pg.connection() as conn:
        cur = await conn.execute(
            "SELECT o.id, o.customer, o.status, o.reason, o.total, o.created_at, o.processed_at, o.processed_by, "
            "string_agg(p.title || ' × ' || oi.qty, ', ' ORDER BY p.title) AS items "
            "FROM orders o "
            "JOIN order_items oi ON oi.order_id = o.id "
            "JOIN products p ON p.id = oi.product_id "
            "GROUP BY o.id ORDER BY o.id DESC LIMIT %s",
            (limit,),
        )
        rows = await cur.fetchall()
    return [{**order_json(row), "customer": row["customer"], "items": row["items"]} for row in rows]


@router.get("/feed")
async def feed(request: Request, limit: int = Query(default=10, ge=1, le=20)) -> list[dict]:
    return await request.app.state.cache.feed(limit)


@router.get("/stats", dependencies=[Depends(require_admin)])
async def stats(request: Request) -> dict:
    async with request.app.state.pg.connection() as conn:
        cur = await conn.execute(
            "SELECT (SELECT count(*) FROM products) AS products, "
            "count(*) FILTER (WHERE status = 'pending') AS pending, "
            "count(*) FILTER (WHERE status = 'confirmed') AS confirmed, "
            "count(*) FILTER (WHERE status = 'rejected') AS rejected, "
            "coalesce(sum(total) FILTER (WHERE status = 'confirmed'), 0) AS revenue, "
            "(SELECT count(*) FROM event_log) AS events "
            "FROM orders"
        )
        row = await cur.fetchone()
    return {**row, "revenue": str(row["revenue"])}
