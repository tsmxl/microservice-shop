S ?= api

init:
	./scripts/init.sh

lock:
	./scripts/lock.sh

build:
	docker compose build

up:
	docker compose up -d --build

down:
	docker compose --profile public down

ps:
	docker compose ps -a

logs:
	docker compose logs -f --tail 100 $(S)

diag:
	./scripts/diag.sh

public:
	docker compose --profile public up -d tailscale

prune:
	docker builder prune -f

demo2:
	./scripts/demo2.sh

smoke:
	./scripts/smoke.sh
