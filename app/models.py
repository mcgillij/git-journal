"""SQLModel database models."""

from datetime import date, datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class Project(SQLModel, table=True):
    """A git repository to monitor and summarize."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    git_url: Optional[str] = None  # Remote URL (for cloning)
    git_path: str  # Local clone path on disk
    branch: str = "main"
    enabled: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_processed_date: Optional[date] = None
    readme_content: Optional[str] = None  # README.md content, markdown format

    articles: list["Article"] = Relationship(back_populates="project")
    reconciliation_logs: list["ReconciliationLog"] = Relationship(
        back_populates="project"
    )


class Article(SQLModel, table=True):
    """An AI-generated daily summary for a project."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    date: date
    title: str = ""
    content: str = ""  # Markdown-formatted summary
    commit_count: int = 0
    files_changed: int = 0
    lines_added: int = 0
    lines_removed: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    project: Project = Relationship(back_populates="articles")


class ReconciliationLog(SQLModel, table=True):
    """A record of a reconciliation run for a project."""

    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    date_scanned: date
    status: str  # "success", "failed", "skipped"
    error_message: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: Project = Relationship(back_populates="reconciliation_logs")


# Forward reference resolution for Article.project relationship
Article.model_rebuild()
Project.model_rebuild()
ReconciliationLog.model_rebuild()
