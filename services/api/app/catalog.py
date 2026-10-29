import logging
from decimal import Decimal
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, Field

from app.admin import require_admin
from app.cache import LIST_KEY, LIST_TTL, PRODUCT_TTL, product_key

log = logging.getLogger("api.catalog")
router = APIRouter()


class ProductIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    artist: str = Field(min_length=1, max_length=200)
    format: Literal["vinyl", "cassette"]
    description: str = Field(default="", max_length=2000)
    price: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    stock: int = Field(ge=0, le=1_000_000)
    drop_at: AwareDatetime
    per_customer_limit: int = Field(ge=1, le=100)


def product_json(row: dict) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "artist": row["artist"],
        "format": row["format"],
        "description": row["description"],
        "price": str(row["price"]),
        "stock": row["stock"],
        "drop_at": row["drop_at"].isoformat(),
        "per_customer_limit": row["per_customer_limit"],
        "updated_at": row["updated_at"].isoformat(),
    }


async def publish(request: Request, event: str, data: dict) -> None:
    try:
        await request.app.state.events.publish(event, data)
    except Exception as exc:
        log.warning("event %s not published: %s", event, exc)


@router.get("/products")
async def list_products(request: Request) -> JSONResponse:
    cache = request.app.state.cache
    products, state = await cache.get(LIST_KEY)
    if products is None:
        async with request.app.state.pg.connection() as conn:
            cur = await conn.execute(
                "SELECT id, title, artist, format, description, price, stock, drop_at, per_customer_limit, updated_at "
                "FROM products ORDER BY drop_at DESC, id"
            )
            products = [product_json(row) for row in await cur.fetchall()]
        await cache.put(LIST_KEY, products, LIST_TTL)
    return JSONResponse(products, headers={"X-Cache": state})


@router.get("/products/{product_id}")
async def get_product(product_id: int, request: Request) -> JSONResponse:
    cache = request.app.state.cache
    key = product_key(product_id)
    product, state = await cache.get(key)
    if product is None:
        async with request.app.state.pg.connection() as conn:
            cur = await conn.execute(
                "SELECT id, title, artist, format, description, price, stock, drop_at, per_customer_limit, updated_at "
                "FROM products WHERE id = %s",
                (product_id,),
            )
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(404, "Релиз не найден")
        product = product_json(row)
        await cache.put(key, product, PRODUCT_TTL)
    return JSONResponse(product, headers={"X-Cache": state})


@router.post("/products", status_code=201, dependencies=[Depends(require_admin)])
async def create_product(payload: ProductIn, request: Request) -> JSONResponse:
    async with request.app.state.pg.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO products (title, artist, format, description, price, stock, drop_at, per_customer_limit) "
            "VALUES (%(title)s, %(artist)s, %(format)s, %(description)s, %(price)s, %(stock)s, %(drop_at)s, "
            "%(per_customer_limit)s) "
            "RETURNING id, title, artist, format, description, price, stock, drop_at, per_customer_limit, updated_at",
            payload.model_dump(),
        )
        product = product_json(await cur.fetchone())
    await request.app.state.cache.delete(LIST_KEY)
    await publish(request, "product.created", product)
    log.info("product %s created", product["id"])
    return JSONResponse(product, status_code=201)


@router.put("/products/{product_id}", dependencies=[Depends(require_admin)])
async def update_product(product_id: int, payload: ProductIn, request: Request) -> JSONResponse:
    async with request.app.state.pg.connection() as conn:
        cur = await conn.execute(
            "UPDATE products SET title = %(title)s, artist = %(artist)s, format = %(format)s, "
            "description = %(description)s, price = %(price)s, stock = %(stock)s, drop_at = %(drop_at)s, "
            "per_customer_limit = %(per_customer_limit)s, updated_at = now() "
            "WHERE id = %(id)s "
            "RETURNING id, title, artist, format, description, price, stock, drop_at, per_customer_limit, updated_at",
            {**payload.model_dump(), "id": product_id},
        )
        row = await cur.fetchone()
    if row is None:
        raise HTTPException(404, "Релиз не найден")
    product = product_json(row)
    await request.app.state.cache.delete(product_key(product_id), LIST_KEY)
    await publish(request, "product.updated", product)
    log.info("product %s updated", product_id)
    return JSONResponse(product)


@router.delete("/products/{product_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_product(product_id: int, request: Request) -> Response:
    try:
        async with request.app.state.pg.connection() as conn:
            cur = await conn.execute("DELETE FROM products WHERE id = %s RETURNING id", (product_id,))
            deleted = await cur.fetchone()
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(409, "У релиза есть заказы, удалить его нельзя. Чтобы снять с продажи, поставьте остаток 0") from None
    if deleted is None:
        raise HTTPException(404, "Релиз не найден")
    await request.app.state.cache.delete(product_key(product_id), LIST_KEY)
    await publish(request, "product.deleted", {"id": product_id})
    log.info("product %s deleted", product_id)
    return Response(status_code=204)
