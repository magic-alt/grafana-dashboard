.PHONY: bootstrap up down observability-up observability-down test validate compose-check integration azure-check

bootstrap:
	@test -f .env || (cp .env.example .env && echo "Created .env; replace the placeholder passwords before running Compose.")

up:
	docker compose up -d --build

down:
	docker compose down

observability-up:
	docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d --build

observability-down:
	docker compose -f docker-compose.yml -f docker-compose.observability.yml down

test:
	pytest

validate:
	python3 scripts/validate_repo.py
	ruff check collector/obs_platform collector/observability_support.py tests scripts/validate_repo.py

compose-check:
	docker compose config >/dev/null
	docker compose -f docker-compose.yml -f docker-compose.observability.yml config >/dev/null

integration:
	bash scripts/ci_integration.sh

azure-check:
	az bicep build --file deploy/azure/main.bicep --stdout >/dev/null
