"""
backend/tests/conftest.py
-------------------------
Pytest configuration and test database isolation fixture.
Ensures all tests execute against an isolated test database (symbios_test.db)
so the development/production database (symbios_enterprise.db) is never polluted.
"""

import os
import pathlib
import pytest

# Point DATABASE_URL to isolated test database before any other backend imports
TEST_DB_PATH = pathlib.Path(__file__).resolve().parent.parent / "symbios_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"

from core.database import engine, Base
from main import seed_initial_enterprise_data


@pytest.fixture(scope="session", autouse=True)
def setup_test_database():
    """Initializes the isolated test database schema and seeds baseline manufacturing data."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    seed_initial_enterprise_data()
    yield
    try:
        engine.dispose()
        if TEST_DB_PATH.exists():
            TEST_DB_PATH.unlink(missing_ok=True)
    except Exception:
        pass
