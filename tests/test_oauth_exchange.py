"""Exercise the real OAuthlib parser with mocked Google HTTP responses."""
import base64
import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from google_auth_oauthlib.flow import Flow
from requests import Request, Response

from backend.auth import google_auth
from backend.storage import cipher, db
from backend.utils.errors import ServiceError


def config():
    return {"web": {"client_id": "test-client.apps.googleusercontent.com", "client_secret": "test-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": "https://oauth2.googleapis.com/token"}}


def mocked_flow(monkeypatch, payload, status=200):
    flow = Flow.from_client_config(config(), scopes=google_auth.SCOPES, code_verifier="v" * 64)
    flow.redirect_uri = "http://localhost:8000/auth/google/callback"
    response = Response()
    response.status_code = status
    response.request = Request("POST", "https://oauth2.googleapis.com/token", data={"code": "fake-code"}).prepare()
    response._content = json.dumps(payload).encode()
    response.headers["Content-Type"] = "application/json"
    monkeypatch.setattr(flow.oauth2session, "request", lambda *args, **kwargs: response)
    return flow


@pytest.mark.parametrize("variant", ["exact", "extra", "aliases", "omitted", "without_assignments"])
def test_real_parser_accepts_required_grants_without_global_scope_bypass(monkeypatch, variant):
    scopes = list(google_auth.SCOPES)
    if variant == "without_assignments":
        scopes.remove(google_auth.ASSIGNMENT_SCOPE)
    if variant == "extra":
        scopes.append("https://www.googleapis.com/auth/calendar.readonly")
    if variant == "aliases":
        scopes = [{value: key for key, value in google_auth.SCOPE_ALIASES.items()}.get(s, s) for s in scopes]
    payload = {"access_token": "fake-access", "refresh_token": "fake-refresh", "token_type": "Bearer", "expires_in": 3600, "scope": " ".join(scopes)}
    if variant == "omitted":
        payload.pop("scope")
    flow = mocked_flow(monkeypatch, payload)
    google_auth.exchange_token(flow, "fake-code")
    credentials = flow.credentials
    google_auth.validate_granted_scopes(credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes)
    assert credentials.refresh_token == "fake-refresh"
    if variant == "without_assignments":
        assert google_auth.ASSIGNMENT_SCOPE not in credentials.scopes


@pytest.mark.parametrize("scopes", ["openid", ""])
def test_missing_permissions_fail_before_credentials_are_used(monkeypatch, scopes):
    flow = mocked_flow(monkeypatch, {"access_token": "fake-access", "token_type": "Bearer", "expires_in": 3600, "scope": scopes})
    with pytest.raises(ServiceError, match="permissions are missing") as result:
        google_auth.exchange_token(flow, "fake-code")
    assert result.value.status_code == 403
    assert "fake-access" not in result.value.message


@pytest.mark.parametrize("error", ["invalid_grant", "invalid_client", "redirect_uri_mismatch"])
def test_provider_errors_have_actionable_redacted_messages(monkeypatch, error):
    flow = mocked_flow(monkeypatch, {"error": error, "error_description": "sensitive-secret-never-display"}, 400)
    with pytest.raises(ServiceError) as result:
        google_auth.exchange_token(flow, "fake-code")
    assert f"[oauth:{error}]" in result.value.message
    assert "sensitive-secret-never-display" not in result.value.message


def test_actual_authorization_challenge_matches_persisted_verifier(configured, monkeypatch):
    monkeypatch.setattr(google_auth, "client_config", config)
    url, binding = google_auth.begin_login()
    params = parse_qs(urlparse(url).query)
    with db() as con:
        row = con.execute("SELECT verifier FROM oauth WHERE state=?", (params["state"][0],)).fetchone()
    verifier = cipher().decrypt(row[0])
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [challenge]
    assert params["code_challenge_method"] == ["S256"]
    assert params["nonce"] == params["state"]
