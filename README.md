# JobPilot 🚀

An AI-powered job application automation platform that scrapes job listings, tailors resumes, writes cover letters, and submits applications — fully autonomously.

---

## Features

- **Automated job scraping** — LinkedIn, Indeed, Monster, Simplify, JobRight
- **AI-driven resume & cover letter generation** — tailored per job using local Ollama models
- **ATS scoring** — keyword matching and gap analysis before applying
- **Greenhouse form automation** — fills all fields (text, dropdowns, file uploads, checkboxes) via Playwright + JavaScript injection
- **Q&A memory** — unknown application questions are stored; you answer them once and the bot reuses answers forever
- **Fully non-blocking** — no human-in-the-loop pausing; unknown questions are queued in the Q&A tab and retried automatically
- **LinkedIn Easy Apply & Indeed Apply** automation
- **Outreach engine** — finds hiring-manager emails via Apollo and sends personalized cold emails
- **Dashboard** — live application status, audit trails, ATS scores, and cover letter previews

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, SQLAlchemy, SQLite |
| Browser automation | Playwright (stealth mode) |
| AI / LLM | Ollama (local), configurable models |
| Vector store | ChromaDB |
| Frontend | Next.js 14, TypeScript, Tailwind CSS, shadcn/ui |
| Task scheduling | APScheduler |

---

## Project Structure

```
jobpilot/
├── backend/
│   ├── agents/          # AI agents: QA engine, cover letter, ATS scorer, job classifier
│   ├── db/              # SQLAlchemy models, schema, DB init
│   ├── routers/         # FastAPI routers: jobs, applications, QA, profile, resumes, outreach
│   ├── scrapers/        # Job scrapers: LinkedIn, Indeed, Monster, Simplify, JobRight
│   ├── services/        # Core services: form filler, Greenhouse, LinkedIn/Indeed apply,
│   │                    #   application orchestration, QA memory, vector store, scheduler
│   ├── config.py        # Settings (env-driven)
│   └── main.py          # FastAPI app entry point
├── frontend/
│   ├── app/             # Next.js app router pages
│   ├── components/      # Reusable UI components
│   └── lib/             # API client, utilities
├── data/                # SQLite DB, resumes, cover letters, screenshots
├── resumes/             # Resume PDFs
├── pyproject.toml
└── Makefile
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+
- [Ollama](https://ollama.ai) running locally (`ollama serve`)
- Playwright browsers (`playwright install chromium`)

### 1. Clone & install

```bash
git clone https://github.com/prempagare07/jobpilot.git
cd jobpilot/jobpilot
pip install -e ".[dev]"
playwright install chromium
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env — set OLLAMA_BASE_URL, LinkedIn credentials, etc.
```

### 3. Initialise the database

```bash
python -m backend.db.init_db
```

### 4. Start the backend

```bash
uvicorn backend.main:app --reload --port 8000
```

### 5. Start the frontend

```bash
cd frontend
npm install
npm run dev
# Open http://localhost:3000
```

---

## Configuration

Key environment variables (see `.env.example` for the full list):

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite DB path | `sqlite:///./data/jobpilot.db` |
| `OLLAMA_BASE_URL` | Ollama API URL | `http://localhost:11434` |
| `OLLAMA_MODEL` | Default LLM model | `llama3.2` |
| `APPLY_BROWSER_HEADLESS` | Run browser headlessly | `true` |
| `APPLY_DAILY_LIMIT` | Max applications per day | `20` |
| `APPLY_REQUIRE_HUMAN_REVIEW` | Gate every submission | `false` |

---

## How It Works

### Application Flow

```
Job URL → ATS Detection → QA Pre-seed (Greenhouse API GET)
       → Playwright fills form (JS-powered: text + selects + checkboxes)
       → Resume & cover letter uploaded
       → Submit
       → If unknown questions → stored in Q&A memory → retry after you answer
```

### Q&A Memory

When the bot encounters a question it can't answer from your profile:
1. It stores the question in the **Q&A Memory** tab (low confidence)
2. The application is marked `needs_human`
3. You answer it once in the Q&A tab
4. Re-trigger the application — it reads from memory and fills it automatically

### Greenhouse Form Filling

The Greenhouse automation uses a layered approach:
1. **API GET** — fetches job questions to pre-populate Q&A memory
2. **Playwright + targeted selectors** — fills known fields by `name` attribute
3. **JavaScript injection** — handles React-controlled selects, hidden elements, and custom components by scanning the full DOM and setting values via native property setters

---

## Development

```bash
# Run backend tests
pytest

# Lint
ruff check backend/
mypy backend/

# Build frontend
cd frontend && npm run build
```

---

## Roadmap

- [ ] Workday application support
- [ ] Multi-profile support (different resumes per role type)
- [ ] Email reply parsing & interview scheduling
- [ ] Chrome extension for one-click apply from any job board
- [ ] Cloud deployment (Docker + Railway/Render)

---

## License

MIT
