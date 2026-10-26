"""Shared pytest fixtures for Brixigo Cloud tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Isolate DB before any app import
_tmp = tempfile.mkdtemp()
_db_path = Path(_tmp) / "pytest_cloud.db"
os.environ["BRIXIGO_CLOUD_DATABASE_URL"] = f"sqlite:///{_db_path.as_posix()}"
os.environ["BRIXIGO_CLOUD_BOOTSTRAP_ADMIN_EMAIL"] = "test@brixigo.com"
os.environ["BRIXIGO_CLOUD_BOOTSTRAP_ADMIN_PASSWORD"] = "TestAdmin!2026"
os.environ["BRIXIGO_CLOUD_BOOTSTRAP_ORG_NAME"] = "Test Organisation"
os.environ["BRIXIGO_CLOUD_COMMAND_HMAC_SECRET"] = "test-command-hmac-secret-32chars!!"
os.environ["BRIXIGO_CLOUD_JWT_SECRET"] = "test-jwt-secret-key-32-characters!!"
os.environ["BRIXIGO_CLOUD_ENVIRONMENT"] = "development"

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, get_db
from app.main import create_app
from app.services.bootstrap import ensure_bootstrap


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def client(settings):
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seed = TestSession()
    try:
        ensure_bootstrap(seed, settings)
        seed.commit()
    finally:
        seed.close()

    app = create_app()

    def override_db():
        db = TestSession()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(client, settings):
    res = client.post(
        "/api/v1/auth/login",
        json={
            "email": settings.bootstrap_admin_email,
            "password": settings.bootstrap_admin_password,
        },
    )
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
