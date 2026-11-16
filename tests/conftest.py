import json

import pytest
from cryptography.fernet import Fernet

from backend.config import settings
from backend.storage import db, initialize


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("GROQ_API_KEY", "")
    settings.cache_clear()
    initialize()
    yield tmp_path
    settings.cache_clear()


@pytest.fixture
def seeded(configured):
    with db() as con:
        for user, cid, name in [("alice", "math", "Math"), ("alice", "physics", "Physics"), ("bob", "math", "Math")]:
            con.execute("INSERT INTO courses VALUES (?,?,?)", (user, cid, json.dumps({"id": cid, "name": name})))
    return configured
