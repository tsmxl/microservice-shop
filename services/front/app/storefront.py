from nicegui import app, ui

from app.api import ApiSession, error_text
from app.theme import (
    FORMAT_ICONS,
    FORMATS,
    STATUSES,
    countdown,
    frame,
    local,
    money,
    parse_time,
    started,
    stock_text,
)

FEED_SECONDS = 3
CATALOG_SECONDS = 15


def product_card(product: dict) -> None:
    drop_at = parse_time(product["drop_at"])
    target = f"/product/{product['id']}"
    with ui.card().props("flat bordered").classes("w-full gap-3 cursor-pointer").on(
        "click", lambda: ui.navigate.to(target)
    ):
        with ui.row().classes("w-full items-center justify-between no-wrap"):
            with ui.row().classes("items-center gap-2"):
                ui.icon(FORMAT_ICONS[product["format"]], color="primary")
                ui.label(FORMATS[product["format"]]).classes("text-grey-5")
            ui.label(money(product["price"])).classes("text-subtitle1 text-weight-medium")
        with ui.column().classes("gap-0"):
            ui.label(product["title"]).classes("text-h6 leading-tight")
            ui.label(product["artist"]).classes("text-secondary")
        if started(drop_at):
            ui.label(stock_text(product["stock"])).classes(
                "text-accent" if product["stock"] == 0 else "text-grey-5"
            )
        else:
            countdown(drop_at)


def render_catalog(products: list[dict] | None) -> None:
    if products is None:
        ui.label("Каталог не загрузился: api или база данных недоступны. Страница обновится сама.").classes(
            "text-accent"
        )
        return
    if not products:
        ui.label("В каталоге пока нет релизов. Добавьте первый в админке.").classes("text-grey-5")
        return
    with ui.grid().classes("w-full grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4"):
        for product in products:
            product_card(product)


def render_feed(entries: list[dict]) -> None:
    if not entries:
        ui.label("Покупок пока не было. Первая появится здесь через секунду после подтверждения.").classes(
            "text-grey-6"
        )
        return
    for entry in entries:
        with ui.row().classes("w-full items-start gap-3 no-wrap"):
            ui.icon(FORMAT_ICONS.get(entry["format"], "album"), color="primary").classes("mt-1")
            with ui.column().classes("gap-0"):
                ui.label(f"{entry['customer']} купил «{entry['title']}», {entry['qty']} шт.")
                ui.label(f"{entry['artist']}, {local(parse_time(entry['at']))}").classes("text-caption text-grey-6")


async def index() -> None:
    api = ApiSession()
    feed_api = ApiSession()
    frame(api, "/")
    layout = ui.row().classes("w-full max-w-7xl mx-auto p-6 gap-8 items-start")
    with layout:
        with ui.column().classes("flex-1 min-w-[280px] gap-4"):
            ui.label("Лимитированные релизы на кассетах и виниле. Тираж ограничен, старт продаж по расписанию.").classes(
                "text-subtitle1 text-grey-4"
            )
            catalog_box = ui.column().classes("w-full")
        with ui.column().classes("w-full lg:w-80 gap-3"):
            ui.label("Только что купили").classes("text-h6")
            feed_box = ui.column().classes("w-full gap-3")

    await ui.context.client.connected()

    async def load_catalog() -> None:
        products = await api.get_json("/products", notify=False)
        catalog_box.clear()
        with catalog_box:
            render_catalog(products)

    async def load_feed() -> None:
        entries = await feed_api.get_json("/feed", notify=False)
        feed_box.clear()
        with feed_box:
            render_feed(entries or [])

    await load_catalog()
    await load_feed()
    with layout:
        ui.timer(CATALOG_SECONDS, load_catalog)
        ui.timer(FEED_SECONDS, load_feed)


def order_status(api: ApiSession, order_id: int, on_done) -> None:
    with ui.row().classes("items-center gap-3"):
        spinner = ui.spinner(color="primary")
        title = ui.label(f"Заказ №{order_id} принят, ждём подтверждения")
    detail = ui.label().classes("text-grey-5")

    async def poll() -> None:
        order = await api.get_json(f"/orders/{order_id}", notify=False)
        if order is None or order["status"] == "pending":
            return
        timer.active = False
        spinner.set_visibility(False)
        title.set_text(f"Заказ №{order_id} {STATUSES[order['status']]}")
        title.classes("text-positive" if order["status"] == "confirmed" else "text-accent")
        if order["status"] == "confirmed":
            detail.set_text(f"Сумма {money(order['total'])}. Обработал {order['processed_by']}")
        else:
            detail.set_text(order["reason"] or "Причина не указана")
        await on_done()

    timer = ui.timer(1, poll)


async def product(product_id: int) -> None:
    api = ApiSession()
    frame(api, "")
    root = ui.column().classes("w-full max-w-3xl mx-auto p-6 gap-4")
    with root:
        ui.button("Все релизы", icon="arrow_back", on_click=lambda: ui.navigate.to("/")).props(
            "flat no-caps color=grey-5"
        )

    await ui.context.client.connected()
    item = await api.get_json(f"/products/{product_id}")
    if item is None:
        with root:
            ui.label("Релиз не найден или api недоступен. Вернитесь к витрине.").classes("text-h6")
        return

    drop_at = parse_time(item["drop_at"])
    limit = item["per_customer_limit"]

    async def refresh_stock() -> None:
        fresh = await api.get_json(f"/products/{product_id}", notify=False)
        if fresh is not None:
            stock_label.set_text(stock_text(fresh["stock"]))
            if fresh["stock"] > 0:
                buy.enable()

    async def place_order() -> None:
        customer = (name.value or "").strip()
        if not customer:
            ui.notify("Введите имя: по нему считается лимит в одни руки", type="warning")
            return
        app.storage.user["customer"] = customer
        buy.disable()
        response = await api.call(
            "POST",
            "/orders",
            json={"customer": customer, "items": [{"product_id": product_id, "qty": int(qty.value or 1)}]},
        )
        if response is None or response.status_code != 202:
            if response is not None:
                ui.notify(error_text(response), type="negative")
            buy.enable()
            return
        status_box.clear()
        with status_box:
            order_status(api, response.json()["id"], refresh_stock)

    with root:
        with ui.card().props("flat bordered").classes("w-full p-6 gap-4"):
            with ui.row().classes("items-center gap-4 no-wrap"):
                ui.icon(FORMAT_ICONS[item["format"]], size="64px", color="primary")
                with ui.column().classes("gap-0"):
                    ui.label(item["title"]).classes("text-h4")
                    ui.label(item["artist"]).classes("text-h6 text-secondary")
            if item["description"]:
                ui.label(item["description"]).classes("text-body1 text-grey-4")
            with ui.row().classes("w-full items-center gap-8"):
                ui.label(money(item["price"])).classes("text-h5 text-primary")
                ui.label(FORMATS[item["format"]]).classes("text-grey-5")
                stock_label = ui.label(stock_text(item["stock"])).classes("text-grey-5")
                ui.label(f"Не больше {limit} шт. в одни руки").classes("text-grey-5")
            ui.label(f"Старт продаж {local(drop_at)}").classes("text-grey-6")

        with ui.card().props("flat bordered").classes("w-full p-6 gap-4"):
            ui.label("Оформить заказ").classes("text-h6")
            with ui.row().classes("w-full items-end gap-4"):
                name = ui.input("Ваше имя", value=app.storage.user.get("customer", "")).classes("flex-1 min-w-[200px]")
                qty = ui.number("Количество", value=1, min=1, max=limit, step=1, precision=0).classes("w-32")
                buy = ui.button("Купить", icon="shopping_bag", on_click=place_order).props("no-caps")
            if not started(drop_at):
                buy.disable()
                countdown(drop_at, size="text-h3", on_start=buy.enable)
            elif item["stock"] == 0:
                buy.disable()
            status_box = ui.column().classes("w-full gap-1")
