"""
models/org.py
-------------
Multi-tenant organizational hierarchy: Companies and Manufacturing Sites/Plants.
"""

import uuid
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from core.database import Base, TimestampMixin


class Company(Base, TimestampMixin):
    __tablename__ = "companies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    registration_code: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    sites = relationship("Site", back_populates="company", cascade="all, delete-orphan")


class Site(Base, TimestampMixin):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id: Mapped[str] = mapped_column(String(36), ForeignKey("companies.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str] = mapped_column(String(255), nullable=True)

    company = relationship("Company", back_populates="sites")
    cameras = relationship("Camera", back_populates="site")
    zones = relationship("SafetyZone", back_populates="site")
    cobots = relationship("Cobot", back_populates="site")
    workers = relationship("Worker", back_populates="site")
    users = relationship("User", back_populates="site")
