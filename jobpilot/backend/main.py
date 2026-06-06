from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.config import PROJECT_ROOT, settings
from backend.db.init_db import init_database
from backend.routers import applications, jobs, outreach, profile, qa, resumes, scraper, stats
from backend.scheduler import start_scheduler, stop_scheduler
from backend.services.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_database()
    start_scheduler()
    await ensure_ollama_models()
    yield
    stop_scheduler()


app = FastAPI(title="JobPilot", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://localhost:3002",
        "http://localhost:3003",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
        "http://127.0.0.1:3002",
        "http://127.0.0.1:3003",
    ],
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1):30[0-9]{2}$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

screenshots_dir = PROJECT_ROOT / "data" / "screenshots"
screenshots_dir.mkdir(parents=True, exist_ok=True)
app.mount("/screenshots", StaticFiles(directory=screenshots_dir, check_dir=False), name="screenshots")

app.include_router(profile.router)
app.include_router(resumes.router)
app.include_router(jobs.router)
app.include_router(applications.router)
app.include_router(qa.router)
app.include_router(outreach.router)
app.include_router(stats.router)
app.include_router(scraper.router)


@app.get("/health")
async def health() -> dict[str, bool | str]:
    return {"status": "ok", "ollama": await ollama_available(), "db": db_available()}


async def ensure_ollama_models() -> None:
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=5.0) as client:
            response = await client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
            local_models = {normalize_model_name(model.get("name", "")) for model in payload.get("models", [])}
            for model_name in (settings.ollama_fast_model, settings.ollama_smart_model):
                if normalize_model_name(model_name) not in local_models:
                    print(f"[startup] pulling Ollama model: {model_name}")
                    await pull_ollama_model(model_name)
    except Exception as exc:
        print(f"[startup] Ollama model check skipped: {exc}")


async def pull_ollama_model(model_name: str) -> None:
    async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=300.0) as client:
        response = await client.post("/api/pull", json={"name": model_name, "stream": False})
        response.raise_for_status()


async def ollama_available() -> bool:
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_base_url, timeout=2.0) as client:
            response = await client.get("/api/tags")
            return response.status_code == 200
    except Exception:
        return False


def db_available() -> bool:
    try:
        with engine.connect() as connection:
            return connection.execute(text("SELECT 1")).scalar() == 1
    except Exception:
        return False


def normalize_model_name(model_name: str) -> str:
    return model_name.removesuffix(":latest")
