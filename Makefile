S ?= api
R ?= api-pg

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

psql:
	docker compose exec postgres psql -U shop -d shop

demo2:
	./scripts/demo2.sh

demo3:
	./scripts/demo3.sh

fw-show:
	docker compose exec firewall fw show

fw-test:
	./scripts/fw-test.sh

fw-break:
	docker compose exec firewall fw break $(R)

fw-restore:
	docker compose exec firewall fw restore

fw-log:
	docker compose exec firewall fw log

smoke:
	./scripts/smoke.sh
