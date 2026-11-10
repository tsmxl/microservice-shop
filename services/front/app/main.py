import os

from nicegui import app, ui

from app import admin, status, storefront
from app.api import client
from app.theme import INSTANCE, TITLE

app.on_shutdown(client.aclose)

ui.page("/")(storefront.index)
ui.page("/product/{product_id}")(storefront.product)
ui.page("/admin")(admin.page)
ui.page("/status")(status.page)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "instance": INSTANCE}


def storage_secret() -> str:
    with open(os.environ["STORAGE_SECRET_FILE"], encoding="utf-8") as f:
        return f.read().strip()


if __name__ in {"__main__", "__mp_main__"}:
    ui.run(
        host="0.0.0.0",
        port=8080,
        title=TITLE,
        favicon="📼",
        dark=True,
        language="ru",
        storage_secret=storage_secret(),
        reload=False,
        show=False,
    )
