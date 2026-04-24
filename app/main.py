"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.database import init_db
from app.routes import index, projects, articles, admin
from app.services.reconciliation import reconcile_all

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database and start scheduler on startup."""
    # Initialize database tables
    init_db()
    logger.info("Database initialized")

    # Start reconciliation scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        reconcile_all,
        "cron",
        hour=settings.reconcile_hour,
        minute=0,
        id="daily_reconciliation",
        name="Daily git journal reconciliation",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(f"Reconciliation scheduled for {settings.reconcile_hour}:00 daily")

    yield

    # Shutdown: shutdown scheduler gracefully
    scheduler.shutdown(wait=False)


app = FastAPI(
    title="Git Journal",
    description="AI-powered daily summaries of git repository activity.",
    version="0.1.0",
    lifespan=lifespan,
)

# Configure Jinja2 templates
templates_dir = Path(__file__).parent / "templates"
app.state.templates = Jinja2Templates(directory=str(templates_dir))

# Mount routes
app.include_router(index.router)
app.include_router(projects.router)
app.include_router(articles.router)
app.include_router(admin.router)


@app.get("/health")
def health_check():
    return {"status": "ok"}
