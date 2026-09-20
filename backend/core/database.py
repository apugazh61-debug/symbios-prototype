"""
core/database.py
----------------
SQLAlchemy 2.0 database engine, session factory, and declarative base.
Supports PostgreSQL (with PostGIS) in production and SQLite for local development.
"""

from datetime import datetime, timezone
import uuid
from typing import Generator
from sqlalchemy import create_engine, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base, Mapped, mapped_column
from core.config import settings

# Engine configuration with connection pooling
connect_args = {"check_same_thread": False} if settings.DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    connect_args=connect_args,
    pool_pre_ping=True
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


class TimestampMixin:
    """Standard audit timestamps for all enterprise entities."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )


def get_db() -> Generator:
    """FastAPI database session dependency with auto-commit/rollback and cleanup."""
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
