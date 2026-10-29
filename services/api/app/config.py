import os
import socket

INSTANCE = socket.gethostname()


def secret(name: str) -> str:
    with open(os.environ[f"{name}_FILE"], encoding="utf-8") as f:
        return f.read().strip()
