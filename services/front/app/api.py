import os

import httpx
from nicegui import ui

API_URL = os.environ.get("API_URL", "http://gateway:8000")

client = httpx.AsyncClient(base_url=API_URL, timeout=5)


def error_text(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        body = None
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        detail = "; ".join(str(item.get("msg", item)) if isinstance(item, dict) else str(item) for item in detail)
    return f"Ошибка {response.status_code}: {detail or response.reason_phrase}"


class ApiSession:
    def __init__(self, token: str | None = None) -> None:
        self.token = token
        self.instance = "—"
        self.cache = "—"

    async def call(self, method: str, path: str, notify: bool = True, **kwargs) -> httpx.Response | None:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = await client.request(method, path, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            self.instance = "недоступен"
            self.cache = "—"
            if notify:
                ui.notify(f"api не отвечает: {type(exc).__name__}", type="negative")
            return None
        self.instance = response.headers.get("x-instance", "—")
        self.cache = response.headers.get("x-cache", "—")
        return response

    async def get_json(self, path: str, notify: bool = True):
        response = await self.call("GET", path, notify=notify)
        if response is None:
            return None
        if response.status_code != 200:
            if notify:
                ui.notify(error_text(response), type="negative")
            return None
        return response.json()
