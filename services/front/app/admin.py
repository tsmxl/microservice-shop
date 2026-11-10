from datetime import UTC, datetime, timedelta

import httpx
from nicegui import app, ui

from app.api import ApiSession, error_text
from app.theme import FORMATS, STATUSES, frame, local, money, parse_local, parse_time

ORDERS_SECONDS = 5

PRODUCT_COLUMNS = [
    {"name": "id", "label": "№", "field": "id", "align": "left", "sortable": True},
    {"name": "title", "label": "Название", "field": "title", "align": "left", "sortable": True},
    {"name": "artist", "label": "Исполнитель", "field": "artist", "align": "left", "sortable": True},
    {"name": "format", "label": "Формат", "field": "format_name", "align": "left"},
    {"name": "price", "label": "Цена", "field": "price_text", "align": "right"},
    {"name": "stock", "label": "Остаток", "field": "stock", "align": "right", "sortable": True},
    {"name": "limit", "label": "Лимит", "field": "per_customer_limit", "align": "right"},
    {"name": "drop", "label": "Старт продаж", "field": "drop_text", "align": "left"},
]

ORDER_COLUMNS = [
    {"name": "id", "label": "№", "field": "id", "align": "left"},
    {"name": "created", "label": "Создан", "field": "created_text", "align": "left"},
    {"name": "customer", "label": "Покупатель", "field": "customer", "align": "left"},
    {"name": "items", "label": "Позиции", "field": "items", "align": "left"},
    {"name": "total", "label": "Сумма", "field": "total_text", "align": "right"},
    {"name": "status", "label": "Статус", "field": "status_text", "align": "left"},
    {"name": "reason", "label": "Причина", "field": "reason", "align": "left"},
    {"name": "worker", "label": "Обработал", "field": "processed_by", "align": "left"},
]


def product_row(product: dict) -> dict:
    return {
        **product,
        "format_name": FORMATS[product["format"]],
        "price_text": money(product["price"]),
        "drop_text": local(parse_time(product["drop_at"])),
    }


def order_row(order: dict) -> dict:
    return {
        **order,
        "created_text": local(parse_time(order["created_at"])),
        "total_text": money(order["total"]),
        "status_text": STATUSES[order["status"]],
        "reason": order["reason"] or "",
        "processed_by": order["processed_by"] or "",
    }


def logout() -> None:
    app.storage.user.pop("admin_token", None)
    ui.navigate.to("/admin")


def login_form(api: ApiSession) -> None:
    async def submit() -> None:
        response = await api.call("POST", "/admin/login", json={"password": password.value or ""})
        if response is None:
            return
        if response.status_code != 200:
            ui.notify(error_text(response), type="negative")
            return
        app.storage.user["admin_token"] = response.json()["token"]
        ui.navigate.to("/admin")

    with ui.card().props("flat bordered").classes("w-full max-w-sm mx-auto p-6 gap-4"):
        ui.label("Вход в админку").classes("text-h6")
        password = ui.input("Пароль", password=True, password_toggle_button=True).classes("w-full")
        password.on("keydown.enter", submit)
        ui.button("Войти", on_click=submit).props("no-caps").classes("w-full")


class AdminPanel:
    def __init__(self, api: ApiSession) -> None:
        self.api = api
        self.products_table = None

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response | None:
        response = await self.api.call(method, path, **kwargs)
        if response is not None and response.status_code == 401:
            ui.notify("Сессия администратора устарела, войдите снова", type="warning")
            logout()
            return None
        return response

    async def fetch(self, path: str, notify: bool = True):
        response = await self.request("GET", path, notify=notify)
        if response is None:
            return None
        if response.status_code != 200:
            if notify:
                ui.notify(error_text(response), type="negative")
            return None
        return response.json()

    def build(self) -> None:
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Админка").classes("text-h5")
            ui.button("Выйти", icon="logout", on_click=logout).props("flat no-caps color=grey-5")
        self.stats_box = ui.row().classes("w-full gap-6")
        with ui.tabs().props("align=left no-caps").classes("w-full") as tabs:
            releases = ui.tab("Релизы", icon="album")
            orders = ui.tab("Заказы", icon="receipt_long")
        with ui.tab_panels(tabs, value=releases).classes("w-full bg-transparent"):
            with ui.tab_panel(releases).classes("gap-3 px-0"):
                with ui.row().classes("gap-2"):
                    ui.button("Добавить релиз", icon="add", on_click=lambda: self.edit(None)).props("no-caps")
                    ui.button("Изменить", icon="edit", on_click=self.edit_selected).props("no-caps outline")
                    ui.button("Удалить", icon="delete", on_click=self.delete_selected).props(
                        "no-caps outline color=accent"
                    )
                self.products_box = ui.column().classes("w-full")
            with ui.tab_panel(orders).classes("gap-3 px-0"):
                ui.label(f"Последние 50 заказов, список обновляется каждые {ORDERS_SECONDS} с").classes(
                    "text-grey-6"
                )
                self.orders_box = ui.column().classes("w-full")
        ui.timer(ORDERS_SECONDS, self.load_orders_and_stats)

    async def load_products(self) -> None:
        products = await self.fetch("/products")
        self.products_box.clear()
        with self.products_box:
            if products is None:
                ui.label("Список релизов не загрузился").classes("text-accent")
                self.products_table = None
                return
            self.products_table = ui.table(
                columns=PRODUCT_COLUMNS,
                rows=[product_row(product) for product in products],
                row_key="id",
                selection="single",
                pagination=20,
            ).props("flat bordered").classes("w-full")

    async def load_orders_and_stats(self) -> None:
        stats = await self.fetch("/stats", notify=False)
        orders = await self.fetch("/orders", notify=False)
        self.stats_box.clear()
        with self.stats_box:
            if stats is not None:
                for label, value in (
                    ("Релизов", stats["products"]),
                    ("В обработке", stats["pending"]),
                    ("Подтверждено", stats["confirmed"]),
                    ("Отклонено", stats["rejected"]),
                    ("Выручка", money(stats["revenue"])),
                    ("Событий в журнале", stats["events"]),
                ):
                    with ui.column().classes("gap-0"):
                        ui.label(str(value)).classes("text-h6 text-primary")
                        ui.label(label).classes("text-caption text-grey-6")
        self.orders_box.clear()
        with self.orders_box:
            if orders is None:
                ui.label("Заказы не загрузились").classes("text-accent")
                return
            if not orders:
                ui.label("Заказов пока нет. Оформите первый на витрине.").classes("text-grey-6")
                return
            ui.table(
                columns=ORDER_COLUMNS,
                rows=[order_row(order) for order in orders],
                row_key="id",
                pagination=20,
            ).props("flat bordered").classes("w-full")

    def selected(self) -> dict | None:
        if self.products_table is None or not self.products_table.selected:
            ui.notify("Сначала отметьте релиз в таблице", type="warning")
            return None
        return self.products_table.selected[0]

    async def edit_selected(self) -> None:
        product = self.selected()
        if product is not None:
            await self.edit(product)

    async def delete_selected(self) -> None:
        product = self.selected()
        if product is None:
            return
        with ui.dialog() as dialog, ui.card().classes("gap-4"):
            ui.label(f"Удалить «{product['title']}»?").classes("text-h6")
            ui.label("Релиз пропадёт с витрины. Если по нему есть заказы, api откажет.").classes("text-grey-5")
            with ui.row().classes("w-full justify-end"):
                ui.button("Отмена", on_click=lambda: dialog.submit(False)).props("flat no-caps")
                ui.button("Удалить релиз", on_click=lambda: dialog.submit(True)).props("no-caps color=accent")
        confirmed = await dialog
        dialog.delete()
        if not confirmed:
            return
        response = await self.request("DELETE", f"/products/{product['id']}")
        if response is None:
            return
        if response.status_code != 204:
            ui.notify(error_text(response), type="negative")
            return
        ui.notify(f"Релиз «{product['title']}» удалён", type="positive")
        await self.load_products()

    async def edit(self, product: dict | None) -> None:
        source = product or {}
        default_drop = datetime.now(UTC) + timedelta(minutes=10)
        with ui.dialog() as dialog, ui.card().classes("w-full max-w-xl gap-3"):
            ui.label("Новый релиз" if product is None else f"Релиз №{product['id']}").classes("text-h6")
            title = ui.input("Название", value=source.get("title", "")).classes("w-full")
            artist = ui.input("Исполнитель", value=source.get("artist", "")).classes("w-full")
            fmt = ui.select(FORMATS, value=source.get("format", "vinyl"), label="Формат").classes("w-full")
            description = ui.textarea("Описание", value=source.get("description", "")).classes("w-full")
            with ui.row().classes("w-full gap-3 no-wrap"):
                price = ui.number("Цена, ₽", value=float(source.get("price", 1990)), min=0, step=10, precision=2)
                stock = ui.number("Остаток", value=source.get("stock", 20), min=0, step=1, precision=0)
                limit = ui.number("Лимит в одни руки", value=source.get("per_customer_limit", 2), min=1, step=1,
                                  precision=0)
            with ui.row().classes("w-full items-end gap-3 no-wrap"):
                drop = ui.input(
                    "Старт продаж, ДД.ММ.ГГГГ ЧЧ:ММ",
                    value=local(parse_time(source["drop_at"]) if "drop_at" in source else default_drop),
                ).classes("flex-1")
                ui.button(
                    "Через 2 минуты",
                    on_click=lambda: drop.set_value(local(datetime.now(UTC) + timedelta(minutes=2))),
                ).props("flat no-caps")

            async def save() -> None:
                try:
                    drop_at = parse_local(drop.value or "")
                except ValueError:
                    ui.notify("Дата старта в формате ДД.ММ.ГГГГ ЧЧ:ММ, например 20.11.2026 18:00", type="warning")
                    return
                body = {
                    "title": (title.value or "").strip(),
                    "artist": (artist.value or "").strip(),
                    "format": fmt.value,
                    "description": (description.value or "").strip(),
                    "price": f"{float(price.value or 0):.2f}",
                    "stock": int(stock.value or 0),
                    "drop_at": drop_at.isoformat(),
                    "per_customer_limit": int(limit.value or 1),
                }
                if product is None:
                    response = await self.request("POST", "/products", json=body)
                else:
                    response = await self.request("PUT", f"/products/{product['id']}", json=body)
                if response is None:
                    return
                if response.status_code not in (200, 201):
                    ui.notify(error_text(response), type="negative")
                    return
                ui.notify(f"Релиз «{body['title']}» сохранён", type="positive")
                dialog.submit(True)

            with ui.row().classes("w-full justify-end"):
                ui.button("Отмена", on_click=lambda: dialog.submit(False)).props("flat no-caps")
                ui.button("Сохранить релиз", on_click=save).props("no-caps")
        saved = await dialog
        dialog.delete()
        if saved:
            await self.load_products()


async def page() -> None:
    token = app.storage.user.get("admin_token")
    api = ApiSession(token)
    frame(api, "/admin")
    root = ui.column().classes("w-full max-w-7xl mx-auto p-6 gap-4")
    await ui.context.client.connected()
    with root:
        if not token:
            login_form(api)
            return
        panel = AdminPanel(api)
        panel.build()
    await panel.load_products()
    await panel.load_orders_and_stats()
