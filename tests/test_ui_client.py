from types import SimpleNamespace
import pytest
import requests
from ui_client import APIError, BackendClient, backend_address


def response(value, status=200):
    return SimpleNamespace(ok=status < 400, status_code=status, json=lambda: value)


def test_only_internal_http_localhost_uses_ipv4():
    assert backend_address("http://localhost:8000/") == "http://127.0.0.1:8000"
    assert backend_address("https://localhost:8000") == "https://localhost:8000"
    assert backend_address("https://classmind.example/api") == "https://classmind.example/api"


def test_cache_reuses_reads_and_mutation_invalidates(monkeypatch):
    calls = []
    def request(session, method, url, **kwargs):
        calls.append((method, url, kwargs))
        return response([{"id": len(calls)}])
    monkeypatch.setattr(requests.Session, "request", request)
    client = BackendClient("http://localhost:8000", "alice")
    first = client.request("GET", "/courses", ttl=30)
    first[0]["id"] = "changed by caller"
    assert client.request("GET", "/courses", ttl=30) == [{"id": 1}]
    assert len(calls) == 1 and calls[0][1].startswith("http://127.0.0.1")
    client.request("POST", "/courses/math/sync")
    assert client.request("GET", "/courses", ttl=30) == [{"id": 3}]
    assert calls[0][2]["timeout"] == (2, 10)


def test_cached_results_do_not_cross_users_or_query_parameters(monkeypatch):
    calls = []
    def request(session, method, url, **kwargs):
        calls.append(kwargs)
        return response(kwargs["headers"]["Authorization"])
    monkeypatch.setattr(requests.Session, "request", request)
    alice, bob = BackendClient("http://localhost:8000", "alice"), BackendClient("http://localhost:8000", "bob")
    assert alice.request("GET", "/history", ttl=30, params={"limit": 20}) == "Bearer alice"
    assert bob.request("GET", "/history", ttl=30, params={"limit": 20}) == "Bearer bob"
    alice.request("GET", "/history", ttl=30, params={"limit": 40})
    assert len(calls) == 3


def test_expiry_refresh_and_401_clear_cache(monkeypatch):
    import ui_client
    clock, status, calls = [0], [200], []
    monkeypatch.setattr(ui_client.time, "monotonic", lambda: clock[0])
    def request(*args, **kwargs):
        calls.append(1)
        return response({"detail": "Session expired"} if status[0] == 401 else [], status[0])
    monkeypatch.setattr(requests.Session, "request", request)
    client = BackendClient("http://localhost:8000", "alice")
    client.request("GET", "/courses", ttl=30)
    clock[0] = 31
    client.request("GET", "/courses", ttl=30)
    client.request("GET", "/courses", ttl=30, refresh=True)
    assert len(calls) == 3
    status[0] = 401
    with pytest.raises(APIError) as error:
        client.request("GET", "/me")
    assert error.value.status == 401 and not client.cache


def test_timeout_has_no_automatic_mutation_retry(monkeypatch):
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        raise requests.Timeout()
    monkeypatch.setattr(requests.Session, "request", request)
    client = BackendClient("http://localhost:8000", "alice")
    with pytest.raises(APIError, match="may still be processing"):
        client.request("POST", "/courses/math/chat", json={"question": "test"})
    assert len(calls) == 1 and calls[0]["timeout"] == (2, 90)
