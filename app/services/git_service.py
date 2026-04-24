"""Git operations: clone, log, diff extraction, and stats."""

import subprocess
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


@dataclass
class GitStats:
    """Summary of changes for a set of commits."""

    commit_count: int = 0
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


@dataclass
class CommitInfo:
    """Information about a single commit."""

    hash: str
    short_hash: str
    author: str
    date: str
    message: str
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0


def run_git(repo_path: str, args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command in the given repository."""
    cmd = ["git", "-C", repo_path] + args
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=check, timeout=120
    )
    return result


def ensure_cloned(repo: dict) -> str:
    """Clone the repository if it doesn't exist locally. Returns local path."""
    git_path = repo["path"]
    branch = repo.get("branch", "main")

    target = Path(git_path).expanduser()

    # If path already exists and is a git repo, use it
    if target.exists() and (target / ".git").exists():
        return str(target)

    # Clone from URL or copy local repo
    url = repo.get("url")
    if not url:
        raise ValueError(f"Repo '{repo['name']}' has no 'url' and path '{git_path}' doesn't exist locally")

    target.parent.mkdir(parents=True, exist_ok=True)

    # Try with token auth first (for private repos), then without
    token_file = Path.home() / ".github-token"
    if token_file.exists():
        token = token_file.read_text().strip()
        authenticated_url = url.replace("https://", f"https://{token}@")
        run_git(str(target.parent), ["clone", "--branch", branch, authenticated_url, git_path])
    else:
        run_git(str(target.parent), ["clone", "--branch", branch, url, git_path])

    return str(target)


def get_commits_for_date(repo_path: str, target_date: date) -> list[CommitInfo]:
    """Get all commits for a specific date."""
    import datetime

    # Use Unix epoch timestamps to avoid timezone interpretation issues.
    # Query a 48-hour window centered on the target date.
    start_dt = datetime.datetime.combine(target_date - datetime.timedelta(days=1), datetime.time(0, 0), tzinfo=datetime.timezone.utc)
    end_dt = datetime.datetime.combine(target_date + datetime.timedelta(days=2), datetime.time(0, 0), tzinfo=datetime.timezone.utc)

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    # Use %ct (committer Unix timestamp) for reliable date comparison
    fmt = "%H%n%an%n%ct%n%s"
    result = run_git(
        repo_path,
        ["log", f"--since={start_ts}", f"--until={end_ts}", f"--format={fmt}"],
    )

    if not result.stdout.strip():
        return []

    lines = result.stdout.strip().split("\n")
    commits = []

    i = 0
    while i < len(lines):
        if i + 3 >= len(lines):
            break
        commit_hash = lines[i]
        author = lines[i + 1]
        committer_ts_str = lines[i + 2]
        message = lines[i + 3]

        # Parse Unix timestamp and filter to target_date
        try:
            ts = int(committer_ts_str)
            commit_dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
            if commit_dt.date() != target_date:
                i += 4
                continue
        except (ValueError, OSError):
            # If we can't parse the timestamp, include it anyway
            pass

        commits.append(CommitInfo(
            hash=commit_hash,
            short_hash=commit_hash[:7],
            author=author,
            date="",  # Will be set from target_date later if needed
            message=message,
        ))
        i += 4

    return commits


def get_commit_diff_stats(repo_path: str, commit_hash: str) -> tuple[int, int, int]:
    """Get files changed, lines added, and lines removed for a single commit."""
    # Try normal diff first (works for non-root commits)
    result = run_git(
        repo_path,
        ["diff", "--numstat", f"{commit_hash}^..{commit_hash}"],
        check=False,
    )

    # If that failed (root commit), use --root without range syntax
    if not result.stdout.strip():
        result = run_git(
            repo_path,
            ["diff", "--numstat", "--root", commit_hash],
            check=False,
        )

    if not result.stdout.strip():
        return 0, 0, 0

    files_changed = 0
    lines_added = 0
    lines_removed = 0

    for line in result.stdout.strip().split("\n"):
        parts = line.split()
        if len(parts) >= 3:
            added = parts[0]
            removed = parts[1]
            # Binary files show as '-'
            if added != "-":
                lines_added += int(added)
            if removed != "-":
                lines_removed += int(removed)
            files_changed += 1

    return files_changed, lines_added, lines_removed


def get_commit_full_diff(repo_path: str, commit_hash: str) -> str:
    """Get the full diff for a single commit."""
    result = run_git(
        repo_path,
        ["diff", f"{commit_hash}^..{commit_hash}", "--unified=3"],
    )
    return result.stdout


def format_git_activity(project_name: str, date: date, branch: str, commits: list[CommitInfo]) -> str:
    """Format git activity into a prompt-friendly string."""
    if not commits:
        return "No commits on this day."

    lines = [f"Project: {project_name}", f"Date: {date.isoformat()}", f"Branch: {branch}", ""]

    for commit in commits:
        lines.append(f"Commit {commit.short_hash} by {commit.author}:")
        lines.append(f"  {commit.message}")
        if commit.files_changed > 0:
            lines.append(
                f"  Files changed: {commit.files_changed}, "
                f"+{commit.lines_added}/-{commit.lines_removed}"
            )
        lines.append("")

    return "\n".join(lines)


def collect_daily_activity(repo_path: str, target_date: date) -> tuple[list[CommitInfo], GitStats]:
    """Collect all commits and stats for a given date. Returns (commits, total_stats)."""
    commits = get_commits_for_date(repo_path, target_date)

    # Enrich each commit with diff stats
    total_added = 0
    total_removed = 0
    total_files = 0

    for commit in commits:
        files_changed, added, removed = get_commit_diff_stats(repo_path, commit.hash)
        commit.files_changed = files_changed
        commit.lines_added = added
        commit.lines_removed = removed
        total_files += files_changed
        total_added += added
        total_removed += removed

    stats = GitStats(
        commit_count=len(commits),
        files_changed=total_files,
        lines_added=total_added,
        lines_removed=total_removed,
    )

    return commits, stats


def get_readme_content(repo_path: str) -> Optional[str]:
    """Extract README.md content from the repository root."""
    readme_files = ["README.md", "README.rst", "README.txt", "README"]
    
    for filename in readme_files:
        result = run_git(
            repo_path,
            ["show", f"HEAD:{filename}"],
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    
    return None
