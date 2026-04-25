"""Gource video generation for project repos."""

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# Where videos are stored (relative to app root)
VIDEO_DIR = Path(__file__).parent.parent.parent / "data" / "videos"


def _check_tools() -> tuple[bool, str]:
    """Check if gource, ffmpeg, and xvfb-run are available."""
    for tool in ("gource", "ffmpeg", "xvfb-run"):
        if not subprocess.run(
            ["which", tool], capture_output=True
        ).returncode == 0:
            return False, f"{tool} is not installed"
    return True, ""


def _get_commit_dates(repo_path: str) -> tuple[str | None, str | None]:
    """Get first and last commit dates as YYYY-MM-DD strings."""
    try:
        # Get all commit timestamps sorted oldest to newest
        result = subprocess.run(
            ["git", "log", "--format=%ct", "--reverse"],
            capture_output=True, text=True, cwd=repo_path, timeout=30
        )
        if result.returncode != 0:
            return None, None

        timestamps = [int(ts) for ts in result.stdout.strip().split("\n") if ts.strip()]
        if not timestamps:
            return None, None

        from datetime import datetime, timezone
        first_date = datetime.fromtimestamp(timestamps[0], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
        last_date = datetime.fromtimestamp(timestamps[-1], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S +0000")
        return first_date, last_date
    except Exception as e:
        logger.error(f"Failed to get commit dates for {repo_path}: {e}")
        return None, None


def video_exists(project_id: str) -> bool:
    """Check if a video file exists for this project."""
    video_path = VIDEO_DIR / f"{project_id}.mp4"
    return video_path.exists() and video_path.stat().st_size > 0


def generate_video(project_name: str, repo_path: str) -> dict:
    """Generate a gource video for the given project.

    Returns a dict with status info:
        {"status": "generated"|"skipped"|"failed", "error": str|None}
    """
    # Check tools availability
    available, error = _check_tools()
    if not available:
        return {"status": "failed", "error": error}

    # Ensure output directory exists
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)

    video_path = VIDEO_DIR / f"{project_name}.mp4"

    # Idempotent: skip if already generated
    if video_exists(project_name):
        return {"status": "skipped", "error": None}

    # Validate repo path
    if not os.path.isdir(repo_path):
        return {"status": "failed", "error": f"Repo path does not exist: {repo_path}"}

    # Get commit date range
    first_date, last_date = _get_commit_dates(repo_path)
    if not first_date or not last_date:
        return {"status": "failed", "error": "No commits found in repo"}

    try:
        # Build gource command (wrapped in xvfb-run for headless OpenGL rendering)
        gource_cmd = [
            "xvfb-run", "--auto-servernum",
            "gource",
            "--log-format", "git",
            "--output-ppm-stream", "-",
            "--output-framerate", "30",
            "--seconds-per-day", "3",
            "--stop-at-time", "60",
            "--viewport", "1920x1080",
            "--background-colour", "000000",
            "--font-size", "16",
            "--title", project_name,
            "--hide", "bloom,progress,date,mouse",
            "--file-idle-time", "3",
        ]

        # Build ffmpeg command
        ffmpeg_cmd = [
            "ffmpeg", "-y",
            "-f", "image2pipe",
            "-vcodec", "ppm",
            "-r", "30",
            "-s", "1920x1080",
            "-i", "-",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            str(video_path),
        ]

        logger.info(f"Generating video for {project_name} ({repo_path})")
        logger.info(f"Gource dates: {first_date} to {last_date}")

        # Run gource piped to ffmpeg
        gource_proc = subprocess.Popen(
            gource_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=repo_path,
        )

        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=gource_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Let gource write to ffmpeg's stdin; close gource's end after pipe
        gource_proc.stdout.close()

        # Wait for both processes with timeout on communicate
        try:
            _, gource_err = gource_proc.communicate(timeout=300)
            ffmpeg_stdout, ffmpeg_stderr = ffmpeg_proc.communicate(timeout=300)
        except subprocess.TimeoutExpired:
            for proc in (gource_proc, ffmpeg_proc):
                try:
                    proc.kill()
                except Exception:
                    pass
            video_path.unlink(missing_ok=True)
            return {"status": "failed", "error": "Generation timed out after 5 minutes"}

        if ffmpeg_proc.returncode != 0:
            err_msg = ffmpeg_stderr.decode("utf-8", errors="replace")[:500]
            logger.error(f"ffmpeg failed for {project_name}: {err_msg}")
            # Clean up partial file
            video_path.unlink(missing_ok=True)
            return {"status": "failed", "error": f"ffmpeg error: {err_msg}"}

        if gource_proc.returncode != 0:
            err_msg = gource_err.decode("utf-8", errors="replace")[:500]
            logger.error(f"gource failed for {project_name}: {err_msg}")
            return {"status": "failed", "error": f"gource error: {err_msg}"}

        # Verify output file
        if video_path.exists() and video_path.stat().st_size > 0:
            size_mb = video_path.stat().st_size / (1024 * 1024)
            logger.info(f"Video generated for {project_name}: {size_mb:.1f}MB")
            return {"status": "generated", "error": None, "size_mb": round(size_mb, 1)}

        return {"status": "failed", "error": "Video file not created"}

    except Exception as e:
        logger.error(f"Unexpected error generating video for {project_name}: {e}")
        video_path.unlink(missing_ok=True)
        return {"status": "failed", "error": str(e)}
