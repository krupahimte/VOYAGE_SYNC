"""
Database engine, session factory and SQLModel table initialisation.
"""

from sqlmodel import SQLModel, create_engine, Session
from app.config import get_settings

settings = get_settings()

# SQLite: connect_args is required for thread-safety in FastAPI
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

engine = create_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=connect_args,
)


def init_db() -> None:
    """Create all tables defined by SQLModel metadata."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """FastAPI dependency that yields a database session per request."""
    with Session(engine) as session:
        yield session
