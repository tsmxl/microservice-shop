from nicegui import ui

from app.api import ApiSession
from app.theme import INSTANCE, frame

REFRESH_SECONDS = 5


def status_row(name: str, state: str) -> None:
    ok = state == "ok"
    with ui.row().classes("items-center gap-2"):
        ui.icon("check_circle" if ok else "error", color="positive" if ok else "negative")
        ui.label(f"{name}: {state}")


def render(hello: dict | None, health: dict | None) -> None:
    with ui.card().props("flat bordered").classes("w-full"):
        ui.label("Ответ api").classes("text-h6")
        if hello is None:
            ui.label("api не отвечает через gateway").classes("text-negative")
        else:
            ui.label(hello["message"])
            ui.label(f"Страницу отдал front {INSTANCE}, данные пришли от api {hello['instance']}")

    with ui.card().props("flat bordered").classes("w-full"):
        ui.label("Зависимости api").classes("text-h6")
        if health is None:
            ui.label("Проверка не выполнена").classes("text-negative")
        else:
            for name, state in health["checks"].items():
                status_row(name, state)

    with ui.card().props("flat bordered").classes("w-full"):
        ui.label("Фоновые обработчики").classes("text-h6")
        workers = (hello or {}).get("workers", [])
        if not workers:
            ui.label("Нет сигналов от worker за последние 30 секунд").classes("text-negative")
        for worker in workers:
            status_row(f"worker {worker['instance']}", "ok")
            ui.label(f"последний сигнал {worker['seconds_ago']} с назад").classes("text-caption text-grey-6")


async def page() -> None:
    api = ApiSession()
    frame(api, "/status")
    with ui.column().classes("w-full max-w-2xl mx-auto p-6 gap-4"):
        ui.label("Состояние стенда").classes("text-h5")
        ui.label(f"Обновляется каждые {REFRESH_SECONDS} секунд").classes("text-grey-6")
        panel = ui.column().classes("w-full gap-4")

    async def refresh() -> None:
        hello = await api.get_json("/hello", notify=False)
        health = await api.get_json("/health", notify=False)
        panel.clear()
        with panel:
            render(hello, health)

    ui.timer(REFRESH_SECONDS, refresh)
