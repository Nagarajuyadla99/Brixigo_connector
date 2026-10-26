"""Fast connector <-> cloud smoke test (no UI, no biometric devices)."""

from __future__ import annotations

import os
import socket
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_tmp = tempfile.mkdtemp()
os.environ["BRIXIGO_CLOUD_DATABASE_URL"] = f"sqlite:///{Path(_tmp) / 'smoke.db'}"
os.environ["BRIXIGO_CLOUD_BOOTSTRAP_ADMIN_EMAIL"] = "smoke@brixigo.com"
os.environ["BRIXIGO_CLOUD_BOOTSTRAP_ADMIN_PASSWORD"] = "SmokeTest!2026"
os.environ["BRIXIGO_CLOUD_JWT_SECRET"] = "smoke-jwt-secret-key-32-characters!"
os.environ["BRIXIGO_CLOUD_COMMAND_HMAC_SECRET"] = "smoke-command-hmac-secret-32chars"
os.environ["BRIXIGO_CLOUD_ENVIRONMENT"] = "development"

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, get_db
from app.main import create_app
from app.services.bootstrap import ensure_bootstrap

PREFIX = "/api/wf/biometric-enterprise"


@pytest.fixture
def cloud():
    settings = get_settings()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    seed = Session()
    try:
        ensure_bootstrap(seed, settings)
        seed.commit()
    finally:
        seed.close()

    app = create_app()

    def override_db():
        db = Session()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, settings
    app.dependency_overrides.clear()


def test_full_connector_cloud_flow(cloud):
    client, settings = cloud

    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/admin").status_code == 200

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "smoke@brixigo.com", "password": "SmokeTest!2026"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    org_id = client.get("/api/v1/tenancy/organisations", headers=auth).json()[0]["id"]

    reg = client.post(
        f"{PREFIX}/connectors/register",
        json={
            "hostname": socket.gethostname(),
            "version": "3.0.0",
            "organisation_id": org_id,
            "name": "Smoke Connector",
        },
    )
    assert reg.status_code == 200, reg.text
    connector_id = reg.json()["connector_id"]
    api_key = reg.json()["api_key"]

    headers = {
        "X-Organisation-Id": org_id,
        "X-Connector-Id": connector_id,
        "X-Device-Token": api_key,
    }

    hb = client.post(
        f"{PREFIX}/connectors/{connector_id}/heartbeat",
        headers=headers,
        json={
            "connector_version": "3.0.0",
            "status": "online",
            "cpu_percent": 10,
            "ram_percent": 30,
            "disk_percent": 40,
            "connected_devices": 0,
            "offline_devices": 0,
            "pending_queue": 0,
        },
    )
    assert hb.status_code == 200
    assert hb.json()["ok"] is True

    cfg = client.get(f"{PREFIX}/connectors/{connector_id}/config", headers=headers)
    assert cfg.status_code == 200
    assert cfg.json()["command_signing_secret"] == settings.command_hmac_secret

    cmds = client.get(f"{PREFIX}/connectors/{connector_id}/commands", headers=headers)
    assert cmds.status_code == 200
    assert "commands" in cmds.json()

    listed = client.get("/api/v1/connectors", headers=auth).json()
    assert any(c["id"] == connector_id for c in listed)
