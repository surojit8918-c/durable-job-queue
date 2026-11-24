"""Shared pytest fixtures for unit and integration testing."""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

import app.db.models  # noqa: F401
from app.core.config import settings
from app.db.base import Base
from app.handlers.partner_api import mock_partner

# Test database engine
test_engine = create_engine(
    settings.TEST_DATABASE_URL,
    pool_pre_ping=True,
)

TestingSessionLocal = sessionmaker(
    bind=test_engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_database() -> Generator[None, None, None]:
    """Create all tables in the test database once per test session."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture(autouse=True)
def clean_tables() -> Generator[None, None, None]:
    """Clean all tables between tests so every test starts with an empty database."""
    with test_engine.connect() as conn, conn.begin():
        conn.execute(text("TRUNCATE TABLE idempotency_keys, job_attempts, jobs CASCADE;"))
    yield


@pytest.fixture(autouse=True)
def reset_mock_partner() -> Generator[None, None, None]:
    """Reset mock partner adapter and persistent state file between tests."""
    mock_partner.clear()
    yield
    mock_partner.clear()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    """Provide a standard transactional database session for tests."""
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
