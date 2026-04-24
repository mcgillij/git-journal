"""Article views and regeneration."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import engine
from app.models import Project, Article
from app.services.ai_service import summarize_git_activity
from app.services.git_service import (
    collect_daily_activity,
    format_git_activity,
)
import markdown as md

router = APIRouter()


@router.get("/projects/{project_id}/articles/{article_date}", response_class=HTMLResponse)
async def article_view(request: Request, project_id: int, article_date: str):
    """View a single article."""
    with Session(engine) as session:
        article = session.execute(
            select(Article).where(
                Article.project_id == project_id,
                Article.date == article_date,
            )
        ).scalars().first()

        if not article:
            return request.app.state.templates.TemplateResponse(
                "404.html", {"request": request}, status_code=404
            )

        proj = session.get(Project, project_id)

    # Render markdown content to HTML
    html_content = md.markdown(article.content, extensions=["tables"])

    return request.app.state.templates.TemplateResponse(
        "article_view.html",
        {
            "request": request,
            "project": proj,
            "article": article,
            "html_content": html_content,
        },
    )


@router.get("/projects/{project_id}/articles/{article_date}/content")
async def article_content_htmx(request: Request, project_id: int, article_date: str):
    """HTMX endpoint — returns just the article content HTML for swapping."""
    with Session(engine) as session:
        article = session.execute(
            select(Article).where(
                Article.project_id == project_id,
                Article.date == article_date,
            )
        ).scalars().first()

        if not article:
            return HTMLResponse("<p>No article found for this date.</p>", status_code=404)

    # Render markdown content to HTML
    html_content = md.markdown(article.content, extensions=["tables"])

    return HTMLResponse(f"""
        <div class="article-content">
            {html_content}
        </div>
        <div style="margin-top: 1rem; font-size: 0.85rem; color: var(--text-dim);">
            <strong>{article.commit_count}</strong> commits · 
            <strong>{article.files_changed}</strong> files changed · 
            +{article.lines_added}/-{article.lines_removed} lines
        </div>
    """)


@router.post("/projects/{project_id}/articles/{article_date}/regenerate")
async def regenerate_article(
    request: Request,
    project_id: int,
    article_date: str,
):
    """Regenerate an article's summary by re-calling the AI."""
    with Session(engine) as session:
        article = session.execute(
            select(Article).where(
                Article.project_id == project_id,
                Article.date == article_date,
            )
        ).scalars().first()

        if not article:
            return HTMLResponse("Article not found", status_code=404)

        proj = session.get(Project, project_id)

        # Re-collect git activity for this date
        commits, stats = collect_daily_activity(proj.git_path, article.date)
        git_activity = format_git_activity(
            proj.name, article.date, proj.branch, commits
        )

        # Generate new summary via AI
        content = summarize_git_activity(
            project_name=proj.name,
            date_str=article_date,
            branch=proj.branch,
            git_activity=git_activity,
        )

        lines = content.split("\n")
        title = lines[0].lstrip("# ").strip() if lines else f"Summary for {article.date}"

        # Update article in DB
        article.content = content
        article.title = title
        article.commit_count = stats.commit_count
        article.files_changed = stats.files_changed
        article.lines_added = stats.lines_added
        article.lines_removed = stats.lines_removed
        session.add(article)
        session.commit()

    # Render markdown for display
    html_content = md.markdown(content, extensions=["tables"])

    return HTMLResponse(html_content)
