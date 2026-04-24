"""Load and parse the repos.yaml configuration file."""

from pathlib import Path
from typing import Optional

import yaml


def load_repos(config_path: Optional[Path] = None) -> list[dict]:
    """Load repo configurations from YAML file.

    Returns a list of dicts with keys: name, path/url, branch, enabled.
    """
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "repos.yaml"

    if not config_path.exists():
        return []

    with open(config_path) as f:
        data = yaml.safe_load(f)

    repos = data.get("repos", [])
    if isinstance(repos, dict):
        # Support both list and dict formats
        repos = [
            {"name": k, **v} for k, v in repos.items()
        ]

    return repos


def get_repo_by_name(repos: list[dict], name: str) -> Optional[dict]:
    """Find a repo config by name."""
    for repo in repos:
        if repo.get("name") == name:
            return repo
    return None
