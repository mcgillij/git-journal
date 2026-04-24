"""SQLite database engine and session management."""

from sqlmodel import SQLModel, Session, create_engine
from app.config import settings


# Create the data directory if it doesn't exist (for SQLite)
settings.data_dir.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.database_url, echo=False)


def init_db():
    """Create all tables. Safe to call multiple times."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """Yield a database session."""
    with Session(engine) as session:
        yield session
