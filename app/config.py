"""Application settings loaded from environment variables."""

from pathlib import Path

# Load .env file if it exists (for local development)
dotenv_path = Path(__file__).parent.parent / ".env"
if dotenv_path.exists():
    from dotenv import load_dotenv
    load_dotenv(dotenv_path)

import os


class Settings:
    """Configuration loaded from environment variables."""

    # AI / LMStudio settings
    ai_base_url: str = os.getenv("AI_BASE_URL", "http://192.168.1.83:1234/v1")
    api_key: str = os.getenv("AI_API_KEY", "lmstudio")
    model_name: str = os.getenv("MODEL_NAME", "qwen3.6-35b-a3b")

    # Reconciliation schedule (hour in 24h format)
    reconcile_hour: int = int(os.getenv("RECONCILE_HOUR", "2"))

    # Database
    database_url: str = os.getenv(
        "DATABASE_URL", "sqlite:///./data/git_journal.db"
    )

    # Paths (relative to app root in container)
    config_dir: Path = Path(__file__).parent.parent.parent / "config"
    repos_file: Path = config_dir / "repos.yaml"
    data_dir: Path = Path(__file__).parent.parent / "data"


settings = Settings()
