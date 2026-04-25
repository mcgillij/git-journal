"""Core reconciliation logic — scans repos for unprocessed days and generates summaries."""

import fcntl
import logging
import os
import time
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

LOCK_FILE = "/tmp/git-journal-reconcile.lock"


def _acquire_lock():
    """Acquire a file lock to prevent concurrent reconciliation runs.

    Returns:
        File descriptor if acquired successfully, None if already locked.
    """
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_WRONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, BlockingIOError):
        return None


def _release_lock(fd):
    """Release the file lock."""
    if fd is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


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
    processed_last_date = last_processed_date  # Track actual last processed date

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

            # Generate summary via AI with retry logic (exponential backoff)
            logger.info(f"[{project_name}] Generating summary for {current_date} ({stats.commit_count} commits)")
            content = _summarize_with_retry(
                project_name=project_name,
                date_str=current_date.isoformat(),
                branch=branch,
                git_activity=git_activity,
            )

            # Validate AI output before saving
            if not content or len(content.strip()) < 20:
                raise ValueError(f"AI returned suspiciously short content ({len(content)} chars)")

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

                # Track the latest processed date (actual last successful date)
                if not processed_last_date or current_date > processed_last_date:
                    processed_last_date = current_date

                session.commit()

            result["dates_processed"] += 1
            logger.info(f"[{project_name}] Saved article for {current_date}")

        except Exception as e:
            error_msg = f"Failed to process {current_date}: {e}"
            logger.error(f"[{project_name}] {error_msg}", exc_info=True)
            result["errors"].append(error_msg)

            # Log the failure for potential retry on next run
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

    # Update last_processed_date in DB to the ACTUAL last processed date (not today)
    with Session(engine) as session:
        proj = session.get(Project, project_id)
        if proj and processed_last_date:
            proj.last_processed_date = processed_last_date
            session.add(proj)
            session.commit()

    return result


def _summarize_with_retry(project_name: str, date_str: str, branch: str,
                          git_activity: str, max_retries: int = 3) -> str:
    """Call the AI summarization service with exponential backoff retry.

    Args:
        project_name: Name of the project.
        date_str: Date string for the article (ISO format).
        branch: Git branch being summarized.
        git_activity: Formatted git activity text.
        max_retries: Maximum number of retry attempts.

    Returns:
        AI-generated markdown summary.

    Raises:
        Exception: The last exception if all retries fail.
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            return summarize_git_activity(
                project_name=project_name,
                date_str=date_str,
                branch=branch,
                git_activity=git_activity,
            )
        except Exception as e:
            last_error = e
            if attempt < max_retries:
                delay = 2 ** attempt  # 2s, 4s, 8s...
                logger.warning(
                    f"[{project_name}] AI call failed (attempt {attempt}/{max_retries}): "
                    f"{e}. Retrying in {delay}s..."
                )
                time.sleep(delay)

    raise last_error


def reconcile_all():
    """Reconcile all enabled projects. Called by APScheduler."""
    logger.info("Starting reconciliation run")

    # Acquire lock to prevent concurrent runs
    fd = _acquire_lock()
    if fd is None:
        logger.warning("Reconciliation already in progress, skipping this run")
        return [{"project": "ALL", "dates_processed": 0, "dates_skipped": 0,
                 "errors": ["Reconciliation already in progress"]}]

    try:
        _do_reconcile_all()
    finally:
        _release_lock(fd)


def _do_reconcile_all():
    """Internal reconciliation logic (runs under lock)."""
    # Load repos from config file and sync with DB
    repo_configs = load_repos()
    config_names = {r["name"] for r in repo_configs}

    with Session(engine) as session:
        # Get all projects from DB
        all_projects = session.execute(select(Project)).scalars().all()
        db_projects = {p.name: p for p in all_projects}

        # Add new repos from config that aren't in DB yet
        for repo_config in repo_configs:
            name = repo_config["name"]
            if name not in db_projects:
                project = Project(
                    name=name,
                    git_url=repo_config.get("url"),
                    git_path=repo_config.get("path", ""),
                    branch=repo_config.get("branch", "main"),
                    enabled=repo_config.get("enabled", True),
                )
                session.add(project)
                db_projects[name] = project

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


def regenerate_article_for_date(project_id: int, target_date: date) -> dict:
    """Regenerate a single article for a specific date.

    Used by the per-article regeneration endpoint.
    Returns a result dict with status info.
    """
    result = {"success": False, "error": None}

    try:
        # Get existing article and project
        with Session(engine) as session:
            article = session.execute(
                select(Article).where(
                    Article.project_id == project_id,
                    Article.date == target_date,
                )
            ).scalars().first()

            if not article:
                result["error"] = "Article not found"
                return result

            proj = session.get(Project, project_id)
            if not proj or not proj.git_path:
                result["error"] = "Project not found or no git path"
                return result

        # Collect git activity for this date
        commits, stats = collect_daily_activity(proj.git_path, target_date)

        if not commits:
            result["error"] = f"No commits found for {target_date}"
            return result

        # Format and summarize with retry logic
        git_activity = format_git_activity(
            proj.name, target_date, proj.branch, commits
        )

        content = _summarize_with_retry(
            project_name=proj.name,
            date_str=target_date.isoformat(),
            branch=proj.branch,
            git_activity=git_activity,
        )

        # Validate AI output
        if not content or len(content.strip()) < 20:
            raise ValueError(f"AI returned suspiciously short content ({len(content)} chars)")

        lines = content.split("\n")
        title = lines[0].lstrip("# ").strip() if lines else f"Summary for {target_date}"

        # Update article in DB
        with Session(engine) as session:
            article.content = content
            article.title = title
            article.commit_count = stats.commit_count
            article.files_changed = stats.files_changed
            article.lines_added = stats.lines_added
            article.lines_removed = stats.lines_removed
            session.add(article)
            session.commit()

        result["success"] = True
    except Exception as e:
        logger.error(f"Failed to regenerate article for {target_date}: {e}", exc_info=True)
        result["error"] = str(e)

    return result
