"""Project detail page with calendar widget."""

from datetime import date, timedelta
from math import ceil
from calendar import monthrange

import markdown as md

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from sqlmodel import Session, select

from app.database import engine
from app.models import Project, Article
from app.services.video_service import video_exists

router = APIRouter()

PAGE_SIZE = 20


def build_calendar(year: int, month: int, article_dates: set) -> list[list[dict]]:
    """Build a calendar grid for the given year/month.

    Returns a 2D list of day cells with metadata.
    Each cell: {"day": int, "has_article": bool, "date_str": str}
    """
    first_day, num_days = monthrange(year, month)
    # first_day is 0=Monday, we want 0=Sunday for display

    calendar = []
    current_row = []

    # Adjust: Python's monthrange returns Monday=0, we want Sunday=0
    start_offset = (first_day + 1) % 7  # Convert to Sunday-first

    # Empty cells before first day
    for _ in range(start_offset):
        current_row.append({"day": None, "has_article": False, "date_str": ""})

    for day in range(1, num_days + 1):
        date_str = f"{year}-{month:02d}-{day:02d}"
        has_article = date_str in article_dates
        current_row.append({
            "day": day,
            "has_article": has_article,
            "date_str": date_str,
        })
        if len(current_row) == 7:
            calendar.append(current_row)
            current_row = []

    # Fill remaining cells in last row
    if current_row:
        while len(current_row) < 7:
            current_row.append({"day": None, "has_article": False, "date_str": ""})
        calendar.append(current_row)

    return calendar


def _render_calendar(year: int, month: int, article_dates: set, project_id: int, request: Request):
    """Render just the calendar widget HTML fragment."""
    from calendar import month_name

    calendar = build_calendar(year, month, article_dates)
    month_label = f"{month_name[month]} {year}"

    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    return request.app.state.templates.TemplateResponse(
        "_calendar_widget.html",
        {
            "request": request,
            "project_id": project_id,
            "current_month": month_label,
            "prev_month_link": f"/projects/{project_id}?month={prev_month}&year={prev_year}",
            "next_month_link": f"/projects/{project_id}?month={next_month}&year={next_year}",
            "calendar": calendar,
        },
    )


@router.get("/projects/{project_id}/calendar/{year}/{month}")
async def project_calendar_fragment(request: Request, project_id: int, year: int, month: int):
    """Return just the calendar widget HTML for HTMX swapping."""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if not project:
            return HTMLResponse("<p>Project not found</p>", status_code=404)

        articles = (
            session.execute(
                select(Article).where(Article.project_id == project_id)
            )
            .scalars()
            .all()
        )

    article_dates = {a.date.isoformat() for a in articles}
    return _render_calendar(year, month, article_dates, project_id, request)


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_detail(request: Request, project_id: int):
    """Project detail page with calendar and article list."""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if not project:
            return request.app.state.templates.TemplateResponse(
                "404.html", {"request": request}, status_code=404
            )

        articles = (
            session.execute(
                select(Article).where(Article.project_id == project_id)
                .order_by(Article.date.desc())
            )
            .scalars()
            .all()
        )

    # Paginate articles (20 per page)
    total_articles = len(articles)
    total_pages = ceil(total_articles / PAGE_SIZE) if total_articles > 0 else 1
    page = max(1, min(int(request.query_params.get("page", 1)), total_pages))

    start_idx = (page - 1) * PAGE_SIZE
    end_idx = start_idx + PAGE_SIZE
    paginated_articles = articles[start_idx:end_idx]

    has_prev = page > 1
    has_next = page < total_pages
    remaining_count = max(0, total_articles - PAGE_SIZE) if page == 1 else 0

    # Build set of dates that have articles (for calendar highlighting)
    article_dates = {a.date.isoformat() for a in articles}

    # Compute summary stats for "no README" fallback
    total_commits = sum(a.commit_count for a in articles) if articles else 0
    date_range_start = articles[-1].date.isoformat() if len(articles) > 5 else None
    date_range_end = articles[0].date.isoformat() if len(articles) > 5 else None
    recent_articles = articles[:5] if len(articles) > 5 else articles

    # Allow month navigation via query params
    today = date.today()
    year = int(request.query_params.get("year", today.year))
    month = int(request.query_params.get("month", today.month))

    # Clamp to valid ranges
    if year < 2000:
        year, month = today.year, today.month
    if month < 1:
        month, year = 12, year - 1
    elif month > 12:
        month, year = 1, year + 1

    calendar = build_calendar(year, month, article_dates)

    # Previous/next month links (preserve page in query)
    prev_month = month - 1 if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = month + 1 if month < 12 else 1
    next_year = year if month < 12 else year + 1

    def _month_link(m, y):
        p = f"&page={page}" if total_pages > 1 else ""
        return f"/projects/{project_id}?month={m}&year={y}{p}"

    prev_month_link = _month_link(prev_month, prev_year)
    next_month_link = _month_link(next_month, next_year)

    # Page navigation links (preserve month/year in query)
    page_prev_link = f"/projects/{project_id}?page={page - 1}&month={month}&year={year}" if has_prev else None
    page_next_link = f"/projects/{project_id}?page={page + 1}&month={month}&year={year}" if has_next else None

    from calendar import month_name
    month_label = f"{month_name[month]} {year}"

    return request.app.state.templates.TemplateResponse(
        "project_detail.html",
        {
            "request": request,
            "project": project,
            "articles": paginated_articles,
            "calendar": calendar,
            "current_month": month_label,
            "prev_month_link": prev_month_link,
            "next_month_link": next_month_link,
            # Pagination
            "page": page,
            "total_pages": total_pages,
            "has_prev": has_prev,
            "has_next": has_next,
            "remaining_count": remaining_count,
            "total_articles": total_articles,
            "page_prev_link": page_prev_link,
            "page_next_link": page_next_link,
            # README content for middle column (rendered to HTML)
            "readme_content": md.markdown(project.readme_content or "", extensions=["tables"]) if project.readme_content else "",
            # Summary stats for no-README fallback
            "total_commits": total_commits,
            "date_range_start": date_range_start,
            "date_range_end": date_range_end,
            "recent_articles": recent_articles,
            # Video URL (if generated)
            "video_url": f"/videos/{project.name}.mp4" if video_exists(project.name) else None,
        },
    )
