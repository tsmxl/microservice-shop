import os
import socket

import httpx
from nicegui import app, ui

TITLE = os.environ.get("SHOP_TITLE", "DemoTape Shop")
API_URL = os.environ.get("API_URL", "http://gateway:8000")
INSTANCE = socket.gethostname()
AMBER = "#f2a33a"

api = httpx.AsyncClient(base_url=API_URL, timeout=3)
app.on_shutdown(api.aclose)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "instance": INSTANCE}


async def fetch(path: str) -> dict:
    try:
        response = await api.get(path)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def status_row(name: str, state: str) -> None:
    ok = state == "ok"
    with ui.row().classes("items-center gap-2"):
        ui.icon("check_circle" if ok else "error", color="positive" if ok else "negative")
        ui.label(f"{name}: {state}")


def render(hello: dict, health_report: dict) -> None:
    with ui.card().classes("w-full"):
        ui.label("Ответ api").classes("text-lg font-bold")
        if "error" in hello:
            ui.label(f"api недоступен: {hello['error']}").classes("text-negative")
        else:
            ui.label(hello["message"])
            ui.label(f"Страницу отдал front {INSTANCE}, данные пришли от api {hello['instance']}")

    with ui.card().classes("w-full"):
        ui.label("Зависимости api").classes("text-lg font-bold")
        if "error" in health_report:
            ui.label(f"Проверка не выполнена: {health_report['error']}").classes("text-negative")
        for name, state in health_report.get("checks", {}).items():
            status_row(name, state)

    with ui.card().classes("w-full"):
        ui.label("Фоновые обработчики").classes("text-lg font-bold")
        workers = hello.get("workers", [])
        if not workers:
            ui.label("Нет сигналов от worker за последние 30 секунд").classes("text-negative")
        for worker in workers:
            status_row(f"worker {worker['instance']}", "ok")
            ui.label(f"последний сигнал {worker['seconds_ago']} с назад").classes("text-caption")


@ui.page("/")
def index() -> None:
    ui.colors(primary=AMBER)
    with ui.column().classes("w-full max-w-2xl mx-auto p-6 gap-4"):
        ui.label(TITLE).classes("text-h3 text-primary")
        ui.label("Лимитированные релизы на кассетах и виниле. Витрина откроется скоро, ниже проверка стенда.")
        panel = ui.column().classes("w-full gap-4")

    async def refresh() -> None:
        hello = await fetch("/hello")
        health_report = await fetch("/health")
        panel.clear()
        with panel:
            render(hello, health_report)

    ui.timer(5, refresh)


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        host="0.0.0.0",
        port=8080,
        title=TITLE,
        favicon="📼",
        dark=True,
        reload=False,
        show=False,
    )
