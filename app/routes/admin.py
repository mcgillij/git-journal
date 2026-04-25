"""Admin page — config display and reconciliation trigger."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.routes.auth import require_admin
from sqlmodel import Session, select

from app.database import engine
from app.models import Project
from app.services.config_loader import load_repos
from app.services.git_service import get_readme_content
from app.services.reconciliation import reconcile_all, reconcile_single
from app.services.video_service import generate_video, video_exists

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, _=Depends(require_admin)):
    """Admin page showing config and reconciliation controls."""
    repo_configs = load_repos()

    with Session(engine) as session:
        projects = session.execute(select(Project)).scalars().all()

    # Pre-compute video existence for each project
    project_videos = {p.name: video_exists(p.name) for p in projects}

    return request.app.state.templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "repos": repo_configs,
            "projects": projects,
            "project_has_video": project_videos,
        },
    )


@router.post("/admin/reconcile")
async def trigger_reconciliation(request: Request, _=Depends(require_admin)):
    """Trigger immediate reconciliation for all enabled repos."""
    results = reconcile_all()

    # Redirect back to admin with a flash message (using query param)
    return RedirectResponse(url="/admin?reconciled=1", status_code=303)


@router.post("/admin/reconcile/{project_name}")
async def trigger_single_reconciliation(request: Request, project_name: str, _=Depends(require_admin)):
    """Trigger reconciliation for a single project."""
    try:
        result = reconcile_single(project_name)
        return RedirectResponse(url=f"/admin?single={result['dates_processed']}", status_code=303)
    except ValueError as e:
        return RedirectResponse(url=f"/admin?error={e}", status_code=303)


@router.post("/admin/fetch-readmes")
async def fetch_readmes(request: Request, _=Depends(require_admin)):
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


@router.post("/admin/generate-videos")
async def generate_videos(request: Request, _=Depends(require_admin)):
    """Generate gource videos for all enabled projects that don't have one yet."""
    with Session(engine) as session:
        projects = session.execute(select(Project)).scalars().all()

    results = []
    for project in projects:
        if not project.enabled or not project.git_path:
            continue

        # Check if video already exists (idempotent)
        if video_exists(project.name):
            results.append({"name": project.name, "status": "skipped", "error": None})
            continue

        result = generate_video(project.name, project.git_path)
        results.append(result)

    # Redirect back to admin with query params showing results
    generated = sum(1 for r in results if r["status"] == "generated")
    skipped = sum(1 for r in results if r["status"] == "skipped")
    failed = sum(1 for r in results if r["status"] == "failed")

    msg = f"Videos: {generated} generated, {skipped} skipped"
    if failed > 0:
        failures = [r for r in results if r["status"] == "failed"]
        err_detail = "; ".join(f"{r['error']}" for r in failures)
        msg += f"; {failed} failed: {err_detail}"

    encoded_msg = quote(msg, safe="")
    return RedirectResponse(url=f"/admin?videos={generated}&skipped={skipped}&msg={encoded_msg}", status_code=303)


@router.post("/admin/generate-video/{project_name}")
async def generate_single_video(request: Request, project_name: str, _=Depends(require_admin)):
    """Generate gource video for a single project."""
    with Session(engine) as session:
        project = session.execute(
            select(Project).where(Project.name == project_name)
        ).scalars().first()

    if not project or not project.git_path:
        return RedirectResponse(url=f"/admin?error={quote(f'Project {project_name} not found')}", status_code=303)

    # Check if video already exists (idempotent)
    if video_exists(project.name):
        return RedirectResponse(url=f"/admin?msg={quote(f'{project.name}: video already exists')}", status_code=303)

    result = generate_video(project.name, project.git_path)
    
    if result["status"] == "generated":
        return RedirectResponse(url=f"/admin?msg={quote(f'{project.name}: video generated successfully')}", status_code=303)
    else:
        err_msg = f"{project.name}: {result.get('error', 'unknown error')}"
        return RedirectResponse(url=f"/admin?error={quote(err_msg)}", status_code=303)
