.PHONY: install db dev-backend dev-frontend dev scrape ollama-pull

install:
	python -m pip install -e ".[dev]"
	playwright install chromium
	cd frontend && npm install

db:
	python -m backend.db.init_db

dev-backend:
	python -m uvicorn backend.main:app --reload --port 8000

dev-frontend:
	cd frontend && npm run dev

dev:
	cd frontend && npx concurrently -n backend,frontend -c cyan,magenta "cd .. && python -m uvicorn backend.main:app --reload --port 8000" "npm run dev"

scrape:
	python -m backend.scrapers.runner

ollama-pull:
	ollama pull llama3.2
	ollama pull llama3.1:8b
