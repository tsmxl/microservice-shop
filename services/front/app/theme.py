import os
import socket
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from nicegui import ui

TITLE = os.environ.get("SHOP_TITLE", "DemoTape Shop")
TZ = ZoneInfo(os.environ.get("SHOP_TZ", "Europe/Moscow"))
INSTANCE = socket.gethostname()

AMBER = "#f2a33a"
TAPE = "#17120e"
SHELL = "#221a14"
LABEL = "#e9dcc3"
REC = "#d6453d"
GREEN = "#7fb069"

FORMATS = {"vinyl": "Винил", "cassette": "Кассета"}
FORMAT_ICONS = {"vinyl": "album", "cassette": "voicemail"}
STATUSES = {"pending": "в обработке", "confirmed": "подтверждён", "rejected": "отклонён"}
TIME_FORMAT = "%d.%m.%Y %H:%M"
NAV = (("/", "Витрина"), ("/admin", "Админка"), ("/status", "Стенд"))


def frame(api, active: str) -> None:
    ui.colors(primary=AMBER, secondary=LABEL, accent=REC, positive=GREEN, negative=REC, dark=SHELL, dark_page=TAPE)
    with ui.header().classes("items-center justify-between px-6 py-3 bg-dark"):
        with ui.row().classes("items-center gap-3 cursor-pointer").on("click", lambda: ui.navigate.to("/")):
            ui.icon("voicemail", size="lg", color="primary")
            ui.label(TITLE).classes("text-h5 text-weight-bold text-primary")
        with ui.row().classes("gap-1"):
            for target, label in NAV:
                ui.button(
                    label,
                    on_click=lambda target=target: ui.navigate.to(target),
                    color="primary" if target == active else "grey-5",
                ).props("flat no-caps")
    with ui.footer().classes("bg-dark text-grey-6 text-caption px-6 py-1 gap-6"):
        ui.label(f"front {INSTANCE}")
        ui.label().bind_text_from(api, "instance", backward=lambda value: f"api {value}")
        ui.label().bind_text_from(api, "cache", backward=lambda value: f"X-Cache {value}")


def money(value: str) -> str:
    text = f"{Decimal(value):,.2f}".replace(",", " ")
    if text.endswith(".00"):
        text = text[:-3]
    return f"{text} ₽"


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def local(value: datetime) -> str:
    return value.astimezone(TZ).strftime(TIME_FORMAT)


def parse_local(text: str) -> datetime:
    return datetime.strptime(text.strip(), TIME_FORMAT).replace(tzinfo=TZ)


def started(drop_at: datetime) -> bool:
    return drop_at <= datetime.now(UTC)


def counter(drop_at: datetime) -> str:
    left = max(0, int((drop_at - datetime.now(UTC)).total_seconds()))
    days, rest = divmod(left, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    clock = f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{days} д {clock}" if days else clock


def stock_text(stock: int) -> str:
    return "Распродано" if stock == 0 else f"Осталось {stock} шт."


def countdown(drop_at: datetime, size: str = "text-h6", on_start=None) -> None:
    with ui.column().classes("gap-0"):
        ui.label("До старта продаж").classes("text-caption text-grey-5")
        label = ui.label(counter(drop_at)).classes(f"{size} font-mono text-primary")

    def tick() -> None:
        if not started(drop_at):
            label.set_text(counter(drop_at))
            return
        timer.active = False
        label.set_text("Продажи открыты")
        if on_start is not None:
            on_start()

    timer = ui.timer(1, tick)
