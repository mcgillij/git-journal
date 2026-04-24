"""Core reconciliation logic — scans repos for unprocessed days and generates summaries."""

import logging
from datetime import date, timedelta

from sqlmodel import Session, select

from app.config import settings
from app.database import engine
from app.models import Project, Article, ReconciliationLog
from app.services.ai_service import summarize_git_activity
from app.services.config_loader import load_repos, get_repo_by_name
from app.services.git_service import (
    ensure_cloned,
    collect_daily_activity,
    format_git_activity,
)

logger = logging.getLogger(__name__)


def reconcile_project(project_name: str, git_path: str, branch: str,
                      project_id: int, git_url: str = None,
                      last_processed_date=None) -> dict:
    """Reconcile a single project — scan for unprocessed days and generate articles.

    Returns a summary dict with results.
    """
    result = {
        "project": project_name,
        "dates_processed": 0,
        "dates_skipped": 0,
        "errors": [],
    }

    try:
        # Ensure repo is cloned locally
        repo_config = {
            "name": project_name,
            "path": git_path,
            "branch": branch,
        }
        if git_url:
            repo_config["url"] = git_url

        local_path = ensure_cloned(repo_config)
    except Exception as e:
        error_msg = f"Clone failed: {e}"
        logger.error(f"[{project_name}] {error_msg}")
        result["errors"].append(error_msg)

        # Log the failure and return early
        with Session(engine) as session:
            log = ReconciliationLog(
                project_id=project_id,
                date_scanned=date.today(),
                status="failed",
                error_message=error_msg,
            )
            session.add(log)
            session.commit()

        return result

    # Determine the date range to scan
    if last_processed_date:
        start_date = last_processed_date + timedelta(days=1)
    else:
        # No previous processing — find first commit date
        import subprocess
        try:
            first_commit_result = subprocess.run(
                ["git", "-C", local_path, "log", "--reverse", "--format=%ad", "--date=short"],
                capture_output=True, text=True, timeout=30,
            )
            if first_commit_result.stdout.strip():
                start_date = date.fromisoformat(first_commit_result.stdout.strip().split("\n")[0])
            else:
                logger.info(f"[{project_name}] No commits found")
                return result
        except Exception as e:
            error_msg = f"Failed to find first commit: {e}"
            logger.error(f"[{project_name}] {error_msg}")
            result["errors"].append(error_msg)
            return result

    today = date.today()
    current_date = start_date
    processed_last_date = last_processed_date

    while current_date <= today:
        try:
            # Check if article already exists for this date
            with Session(engine) as session:
                existing = (
                    session.execute(
                        select(Article).where(
                            Article.project_id == project_id,
                            Article.date == current_date,
                        )
                    )
                    .scalars()
                    .first()
                )

            if existing:
                result["dates_skipped"] += 1
                logger.debug(f"[{project_name}] Already have article for {current_date}")
                current_date += timedelta(days=1)
                continue

            # Collect git activity for this day
            commits, stats = collect_daily_activity(local_path, current_date)

            if not commits:
                result["dates_skipped"] += 1
                logger.debug(f"[{project_name}] No commits on {current_date}")
                current_date += timedelta(days=1)
                continue

            # Format activity for AI prompt
            git_activity = format_git_activity(
                project_name, current_date, branch, commits
            )

            # Generate summary via AI
            logger.info(f"[{project_name}] Generating summary for {current_date} ({stats.commit_count} commits)")
            content = summarize_git_activity(
                project_name=project_name,
                date_str=current_date.isoformat(),
                branch=branch,
                git_activity=git_activity,
            )

            # Generate a title from the first line of content or use a default
            lines = content.split("\n")
            title = lines[0].lstrip("# ").strip() if lines else f"Summary for {current_date}"

            # Save article to database
            with Session(engine) as session:
                article = Article(
                    project_id=project_id,
                    date=current_date,
                    title=title,
                    content=content,
                    commit_count=stats.commit_count,
                    files_changed=stats.files_changed,
                    lines_added=stats.lines_added,
                    lines_removed=stats.lines_removed,
                )
                session.add(article)

                # Track the latest processed date
                if not processed_last_date or current_date > processed_last_date:
                    processed_last_date = current_date

                session.commit()

            result["dates_processed"] += 1
            logger.info(f"[{project_name}] Saved article for {current_date}")

        except Exception as e:
            error_msg = f"Failed to process {current_date}: {e}"
            logger.error(f"[{project_name}] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)

            # Log the failure
            with Session(engine) as session:
                log = ReconciliationLog(
                    project_id=project_id,
                    date_scanned=current_date,
                    status="failed",
                    error_message=str(e),
                )
                session.add(log)
                session.commit()

        current_date += timedelta(days=1)

    # Update last_processed_date in DB if we processed anything
    with Session(engine) as session:
        proj = session.get(Project, project_id)
        if proj and result["dates_processed"] > 0:
            proj.last_processed_date = today
            session.add(proj)
            session.commit()

    return result


def reconcile_all():
    """Reconcile all enabled projects. Called by APScheduler."""
    logger.info("Starting reconciliation run")

    # Load repos from config file and sync with DB
    repo_configs = load_repos()
    config_names = {r["name"] for r in repo_configs}

    with Session(engine) as session:
        # Get all projects from DB
        all_projects = session.execute(select(Project)).scalars().all()
        db_projects = {p.name: p for p in all_projects}

        # Sync enabled/disabled state based on config file
        for name, project in db_projects.items():
            config_repo = get_repo_by_name(repo_configs, name)
            if config_repo is not None:
                project.enabled = config_repo.get("enabled", True)
                session.add(project)
            else:
                # Repo removed from config — mark as inactive
                project.enabled = False
                session.add(project)

        session.commit()

    # Get enabled projects
    with Session(engine) as session:
        enabled_projects = (
            session.execute(
                select(Project).where(Project.enabled == True)  # noqa: E712
            )
            .scalars()
            .all()
        )

    logger.info(f"Reconciling {len(enabled_projects)} enabled projects")

    all_results = []
    for project in enabled_projects:
        try:
            result = reconcile_project(
                project_name=project.name,
                git_path=project.git_path,
                branch=project.branch,
                project_id=project.id,
                git_url=project.git_url,
                last_processed_date=project.last_processed_date,
            )
            all_results.append(result)
        except Exception as e:
            logger.error(f"[{project.name}] Reconciliation failed: {e}", exc_info=True)
            all_results.append({
                "project": project.name,
                "dates_processed": 0,
                "dates_skipped": 0,
                "errors": [str(e)],
            })

    # Log summary
    total_processed = sum(r["dates_processed"] for r in all_results)
    total_errors = sum(len(r.get("errors", [])) for r in all_results)
    logger.info(
        f"Reconciliation complete: {total_processed} articles generated, "
        f"{total_errors} errors across {len(all_results)} projects"
    )

    return all_results


def reconcile_single(project_name: str):
    """Reconcile a single project by name. Used for manual trigger."""
    with Session(engine) as session:
        project = session.execute(
            select(Project).where(Project.name == project_name)
        ).scalars().first()

        if not project:
            raise ValueError(f"Project '{project_name}' not found")

        return reconcile_project(
            project_name=project.name,
            git_path=project.git_path,
            branch=project.branch,
            project_id=project.id,
            git_url=project.git_url,
            last_processed_date=project.last_processed_date,
        )
