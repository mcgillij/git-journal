"""Article views and regeneration."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.config import settings
from app.database import engine
from app.models import Project, Article
from app.services.reconciliation import regenerate_article_for_date
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
    """Regenerate an article's summary by re-calling the AI (with retry logic)."""
    from datetime import date as dt_date

    target = dt_date.fromisoformat(article_date)
    result = regenerate_article_for_date(project_id, target)

    if not result["success"]:
        return HTMLResponse(
            f"<p style='color: var(--red);'>Regeneration failed: {result['error']}</p>",
            status_code=500,
        )

    # Re-fetch the updated article and render markdown for display
    with Session(engine) as session:
        article = session.execute(
            select(Article).where(
                Article.project_id == project_id,
                Article.date == target,
            )
        ).scalars().first()

    html_content = md.markdown(article.content, extensions=["tables"])

    return HTMLResponse(html_content)
