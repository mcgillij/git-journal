"""Admin page — config display and reconciliation trigger."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import engine
from app.models import Project
from app.services.config_loader import load_repos
from app.services.git_service import get_readme_content
from app.services.reconciliation import reconcile_all

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Admin page showing config and reconciliation controls."""
    repo_configs = load_repos()

    with Session(engine) as session:
        projects = session.execute(select(Project)).scalars().all()

    return request.app.state.templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "repos": repo_configs,
            "projects": projects,
        },
    )


@router.post("/admin/reconcile")
async def trigger_reconciliation(request: Request):
    """Trigger immediate reconciliation for all enabled repos."""
    results = reconcile_all()

    # Redirect back to admin with a flash message (using query param)
    return RedirectResponse(url="/admin?reconciled=1", status_code=303)


@router.post("/admin/fetch-readmes")
async def fetch_readmes(request: Request):
    """Fetch README.md content for all enabled projects."""
    with Session(engine) as session:
        projects = session.execute(select(Project)).scalars().all()

    fetched_count = 0
    errors = []

    for project in projects:
        if not project.enabled or not project.git_path:
            continue
        
        try:
            readme = get_readme_content(project.git_path)
            if readme:
                # Truncate to first 2000 chars for display, keep full version
                project.readme_content = readme[:3000] + ("\n\n..." if len(readme) > 3000 else "")
                fetched_count += 1
        except Exception as e:
            errors.append(f"{project.name}: {e}")

    with Session(engine) as session:
        for project in projects:
            if project.readme_content is not None:
                session.add(project)
        session.commit()

    # Redirect back to admin
    msg = f"Fetched READMEs for {fetched_count} projects"
    err_msg = f"; Errors: {', '.join(errors)}" if errors else ""
    return RedirectResponse(url=f"/admin?readmes={fetched_count}{err_msg}", status_code=303)
