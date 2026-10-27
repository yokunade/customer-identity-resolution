.PHONY: demo up down reset logs seed users test bench ai-export help

help:            ## Show this help
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  make %-8s %s\n", $$1, $$2}'

demo:            ## Build and start everything, load demo data, then follow the seed log
	docker compose up --build -d
	docker compose logs -f seed

up:              ## Start without rebuilding
	docker compose up -d

down:            ## Stop (keeps the data)
	docker compose down

reset:           ## Stop and delete all data, then start fresh
	docker compose down -v
	$(MAKE) demo

logs:            ## Follow API logs
	docker compose logs -f api

seed:            ## Re-run the demo loader (skips if data exists)
	docker compose run --rm seed

users:           ## List reviewer accounts
	docker compose exec api python -m app.review.users list

ai-export:       ## Export AI reviewer data from the demo (masked) into ./exports
	mkdir -p exports
	docker compose exec -T api python -m app.review.export --format chat --source all --split train > exports/train.jsonl
	docker compose exec -T api python -m app.review.export --format eval --source all --split test > exports/test.jsonl
	docker compose exec -T api python -m app.review.export --format prompt --source all --shots 6 > exports/prompt.md
	python -m benchmark.score_ai_reviewer --eval exports/test.jsonl --baseline no_conflict

test:            ## Run the test suite (needs TEST_DATABASE_URL pointing at a throwaway database)
	pytest

bench:           ## Generate and run the 200k benchmark against the compose database (slow)
	docker compose exec db createdb -U identity identity_bench 2>/dev/null || true
	python -m benchmark.generate --records 200000 --out /tmp/data200k.jsonl
	python -m benchmark.run --data /tmp/data200k.jsonl --db postgresql://identity:identity@localhost:5433/identity_bench --out /tmp/bench
	python -m benchmark.evaluate --data /tmp/data200k.jsonl --results /tmp/bench --db postgresql://identity:identity@localhost:5433/identity_bench --out /tmp/bench/report.json
