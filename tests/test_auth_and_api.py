import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import storage
from backend.auth import google_auth
from backend.main import app
from backend.utils.errors import ServiceError


def test_tokens_encrypted_sessions_hashed_and_expire(configured):
    storage.save_user("alice", {"name": "Alice"}, '{"refresh_token":"sensitive-token"}')
    token = storage.create_session("alice")
    with storage.db() as con:
        row = con.execute("SELECT credentials FROM users").fetchone()
        session = con.execute("SELECT hash FROM sessions").fetchone()
        assert b"sensitive-token" not in row[0]
        assert token != session[0]
        con.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
    assert storage.session_user(token) is None
    assert "sensitive-token" in storage.get_user("alice")[1]


def test_oauth_rejects_wrong_browser_and_expired_state(configured):
    with storage.db() as con:
        con.execute("INSERT INTO oauth VALUES (?,?,?,?)", ("state", storage.digest("browser"), storage.cipher().encrypt(b"verifier"), time.time() + 60))
    with pytest.raises(ServiceError, match="verification failed"):
        google_auth.finish_login("state", "attacker", "code")
    with storage.db() as con:
        con.execute("UPDATE oauth SET expires=0")
    with pytest.raises(ServiceError, match="expired"):
        google_auth.finish_login("state", "browser", "code")


def test_refresh_persists_new_credentials(configured, monkeypatch):
    storage.save_user("alice", {"name": "Alice"}, "{}")
    class FakeCredentials:
        valid = False
        refresh_token = "refresh"
        def refresh(self, request):
            self.valid = True
        def to_json(self):
            return '{"token":"renewed"}'
    fake = FakeCredentials()
    monkeypatch.setattr(google_auth.Credentials, "from_authorized_user_info", lambda *_: fake)
    assert google_auth.get_credentials("alice").valid
    assert "renewed" in storage.get_user("alice")[1]


def test_saved_credentials_do_not_regain_declined_assignment_scope(configured):
    from datetime import datetime, timedelta, timezone
    from google.oauth2.credentials import Credentials
    credentials = Credentials(token="test-access", refresh_token="test-refresh",
                              token_uri="https://oauth2.googleapis.com/token", client_id="test-client",
                              client_secret="test-secret", scopes=google_auth.REQUIRED_SCOPES,
                              expiry=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))
    storage.save_user("alice", {"name": "Alice"}, credentials.to_json())
    restored = google_auth.get_credentials("alice")
    assert google_auth.ASSIGNMENT_SCOPE not in restored.scopes
    assert set(google_auth.REQUIRED_SCOPES) == set(restored.scopes)


def test_api_requires_auth_and_isolates_history_and_jobs(seeded):
    alice = storage.create_session("alice")
    bob = storage.create_session("bob")
    storage.append_exchange("alice", "math", "private question", {"content": "private answer", "sources": []})
    with storage.db() as con:
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", ("private-job", "alice", "math", "completed", "{}"))
    with TestClient(app) as client:
        assert client.get("/courses").status_code == 401
        assert client.get("/courses", cookies={"classmind_session": alice}).status_code == 401
        headers = {"Authorization": "Bearer " + bob}
        assert client.get("/jobs/private-job", headers=headers).status_code == 404
        assert client.get("/courses/physics/history", headers=headers).status_code == 404
        assert client.get("/courses/math/history", headers=headers).json() == []
        alice_headers = {"Authorization": "Bearer " + alice}
        assert len(client.get("/courses/math/history", headers=alice_headers).json()) == 2
        assert client.post("/auth/logout", headers=alice_headers).status_code == 200
        assert client.get("/courses", headers=alice_headers).status_code == 401


def test_chat_rejects_blank_and_does_not_persist_provider_failure(seeded, monkeypatch):
    import backend.main as main
    token = storage.create_session("alice")
    headers = {"Authorization": "Bearer " + token}
    def fail(*args):
        raise ServiceError("Provider unavailable", 503)
    monkeypatch.setattr(main, "answer_question", fail)
    with TestClient(app) as client:
        assert client.post("/courses/math/chat", headers=headers, json={"question": "  "}).status_code == 422
        assert client.post("/courses/math/chat", headers=headers, json={"question": "test"}).status_code == 503
    assert storage.history("alice", "math") == []


def test_oauth_success_consumes_state_and_sets_secure_cookie(configured, monkeypatch):
    from backend.auth.google_auth import SCOPES
    captured = {}
    class FakeCredentials:
        id_token = "signed-google-token"
        granted_scopes = SCOPES
        refresh_token = "refresh"
        def to_json(self):
            return '{"refresh_token":"refresh"}'
    class FakeFlow:
        credentials = FakeCredentials()
        oauth2session = SimpleNamespace(register_compliance_hook=lambda *_: None)
        def fetch_token(self, **kwargs):
            captured.update(kwargs)
    def factory(config, **kwargs):
        captured.update(kwargs)
        return FakeFlow()
    monkeypatch.setattr(google_auth, "client_config", lambda: {"web": {"client_id": "client"}})
    monkeypatch.setattr(google_auth.Flow, "from_client_config", factory)
    monkeypatch.setattr(google_auth.id_token, "verify_oauth2_token",
                        lambda *_, **kwargs: {"sub": "alice", "nonce": "state", "name": "Alice"})
    with TestClient(app) as client:
        with storage.db() as con:
            con.execute("INSERT INTO oauth VALUES (?,?,?,?)", ("state", storage.digest("browser"), storage.cipher().encrypt(b"pkce-verifier"), time.time() + 60))
        client.cookies.set("classmind_oauth", "browser")
        response = client.get("/auth/google/callback?state=state&code=auth-code", follow_redirects=False)
        assert response.status_code == 303
        cookie = response.headers.get_list("set-cookie")[0]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert "auth-code" not in response.headers["location"]
        assert captured["code_verifier"] == "pkce-verifier"
        assert captured["code"] == "auth-code"
        token = response.cookies["classmind_session"]
        assert storage.session_user(token) == "alice"
        assert client.get("/auth/google/callback?state=state&code=auth-code").status_code == 400
