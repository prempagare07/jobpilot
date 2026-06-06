# JobPilot

JobPilot is a local-first job application automation scaffold for Apple Silicon Macs. It combines a FastAPI backend, SQLite application state, ChromaDB vector memory, local Ollama models, a Next.js 14 dashboard, APScheduler jobs, and Playwright-based scraping and browser automation.

## Stack

- Backend: Python 3.12, FastAPI, SQLAlchemy 2.0
- Database: SQLite at `data/jobpilot.db`, ChromaDB at `data/chroma/`
- LLM: Ollama at `http://localhost:11434`
- Models: `llama3.2` for quick tasks, `llama3.1:8b` for cover letters and outreach
- Frontend: Next.js 14 App Router, Tailwind CSS, shadcn-style UI components
- Scheduler: APScheduler 3.x
- Scraping: Playwright, stealth plugin, httpx, BeautifulSoup, feedparser

## Setup

```bash
cd jobpilot
cp .env.example .env
make install
make ollama-pull
make db
```

## Development

```bash
make dev
```

The backend runs on `http://localhost:8000` and the frontend runs on `http://localhost:3000`.

Useful commands:

```bash
make dev-backend
make dev-frontend
make scrape
```

## API

- `GET /health`
- `GET /api/profile`
- `PUT /api/profile`
- `GET /api/jobs`
- `PATCH /api/jobs/{job_id}/status`
- `POST /api/resumes`
- `GET /api/applications`
- `POST /api/applications`
- `POST /api/qa/answer`
- `POST /api/qa/teach`
- `GET /api/scheduler/status`
- `POST /api/scheduler/scrape`

## Data

`data/`, `.env`, and uploaded resume files are ignored by git. The project keeps `.gitkeep` files so the expected local folders exist after cloning.
