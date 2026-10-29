import json
import logging
import time

from redis.asyncio import Redis
from redis.exceptions import RedisError

log = logging.getLogger("api.cache")

PRODUCT_TTL = 300
LIST_TTL = 60
LIST_KEY = "products:all"
FEED_KEY = "feed:recent"
BYPASS_SECONDS = 10


def product_key(product_id: int) -> str:
    return f"product:{product_id}"


class Cache:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.bypass_until = 0.0

    def available(self) -> bool:
        return time.monotonic() >= self.bypass_until

    def failed(self, exc: Exception) -> None:
        log.warning("redis error, cache bypassed for %s s: %s", BYPASS_SECONDS, exc)
        self.bypass_until = time.monotonic() + BYPASS_SECONDS

    async def get(self, key: str) -> tuple[object | None, str]:
        if not self.available():
            return None, "BYPASS"
        try:
            raw = await self.redis.get(key)
        except (RedisError, OSError) as exc:
            self.failed(exc)
            return None, "BYPASS"
        if raw is None:
            return None, "MISS"
        return json.loads(raw), "HIT"

    async def put(self, key: str, value: object, ttl: int) -> None:
        if not self.available():
            return
        try:
            await self.redis.setex(key, ttl, json.dumps(value, ensure_ascii=False))
        except (RedisError, OSError) as exc:
            self.failed(exc)

    async def delete(self, *keys: str) -> None:
        try:
            await self.redis.delete(*keys)
        except (RedisError, OSError) as exc:
            self.failed(exc)

    async def feed(self, limit: int) -> list[dict]:
        if not self.available():
            return []
        try:
            entries = await self.redis.lrange(FEED_KEY, 0, limit - 1)
        except (RedisError, OSError) as exc:
            self.failed(exc)
            return []
        return [json.loads(entry) for entry in entries]
