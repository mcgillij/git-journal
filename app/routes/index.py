"""Index page — list all projects."""

from datetime import date

import markdown as md
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.database import engine
from app.models import Project, Article
from app.services.config_loader import load_repos

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Index page showing all projects with article counts."""
    # Load repos from config file
    repo_configs = load_repos()
    config_names = {r["name"] for r in repo_configs}

    # Get DB data
    with Session(engine) as session:
        db_projects = session.execute(select(Project)).scalars().all()

        # Count articles per project
        article_counts = {}
        last_dates = {}
        for proj in db_projects:
            count = session.execute(
                select(Article).where(Article.project_id == proj.id)
            ).scalars().all()
            article_counts[proj.name] = len(count)

            # Get most recent article date
            latest = session.execute(
                select(Article.date)
                .where(Article.project_id == proj.id)
                .order_by(Article.date.desc())
                .limit(1)
            ).scalar()
            last_dates[proj.name] = latest

    # Merge config state with DB data
    projects = []
    for repo_config in repo_configs:
        name = repo_config["name"]
        db_proj = next((p for p in db_projects if p.name == name), None)

        projects.append({
            "name": name,
            "path": repo_config.get("path", ""),
            "url": repo_config.get("url", ""),
            "branch": repo_config.get("branch", "main"),
            "enabled": repo_config.get("enabled", True),
            "in_db": db_proj is not None,
            "article_count": article_counts.get(name, 0),
            "last_processed_date": last_dates.get(name),
            "db_project_id": db_proj.id if db_proj else None,
            "readme_content": db_proj.readme_content if db_proj else None,
            "readme_html": md.markdown(db_proj.readme_content or "", extensions=["tables"]) if db_proj and db_proj.readme_content else "",
        })

    # Add any DB projects not in config (orphaned)
    for proj in db_projects:
        if proj.name not in config_names:
            projects.append({
                "name": proj.name,
                "path": proj.git_path,
                "url": proj.git_url or "",
                "branch": proj.branch,
                "enabled": proj.enabled,
                "in_db": True,
                "article_count": article_counts.get(proj.name, 0),
                "last_processed_date": last_dates.get(proj.name),
                "db_project_id": proj.id,
                "readme_content": proj.readme_content,
                "readme_html": md.markdown(proj.readme_content or "", extensions=["tables"]) if proj.readme_content else "",
            })

    return request.app.state.templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "projects": projects,
            "today": date.today().isoformat(),
        },
    )
