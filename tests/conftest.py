import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture(scope="session")
def integration_engine():
    if os.getenv("LITLENS_INTEGRATION") != "1":
        pytest.skip("Set LITLENS_INTEGRATION=1 with PostgreSQL and Redis available")
    from litlens.db import models

    name = "litlens_test_" + uuid4().hex
    admin = create_engine(models.engine.url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f"CREATE DATABASE {name}"))
    engine = create_engine(models.engine.url.set(database=name))
    original = models.engine
    models.engine = engine
    try:
        command.upgrade(Config("alembic.ini"), "head")
        yield engine
    finally:
        models.engine = original
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f"DROP DATABASE {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture
def api(integration_engine, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    from litlens.config import settings
    from litlens.db.models import Base
    from litlens.main import app

    factory = sessionmaker(bind=integration_engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr("litlens.api.dependencies.SessionLocal", factory)
    monkeypatch.setattr("litlens.worker.SessionLocal", factory)
    monkeypatch.setattr(settings, "api_tokens", {"alice-token": "alice", "bob-token": "bob"})
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "retry_seconds", 0)
    # Cleanup only this freshly created, randomly named test database.
    with integration_engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    with TestClient(app, headers={"Authorization": "Bearer alice-token"}) as client:
        yield client, factory
