import json
from types import SimpleNamespace

from backend import main, storage


def test_dashboard_omits_large_file_reports_and_old_jobs(seeded):
    storage.save_manifest("alice", "math", {"files": [{"status": "imported"}], "chunks": 9, "synced_at": 100})
    with storage.db() as con:
        for jid in ("old", "new"):
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", (jid, "alice", "math", "completed",
                        json.dumps({"progress": 1, "message": "Done", "files": [{"text": "x" * 10000}]})))
    courses = main.course_summaries("alice")
    math = next(c for c in courses if c["id"] == "math")
    assert math["job"]["id"] == "new"
    assert "files" not in math["job"]
    assert math["chunks"] == 9 and math["documents"] == 1
    assert len(json.dumps(courses)) < 2000


def test_refresh_does_not_start_bulk_imports_by_default(seeded, monkeypatch):
    monkeypatch.setattr(main, "fetch_courses", lambda _: [{"id": "math"}, {"id": "physics"}])
    calls = []
    monkeypatch.setattr(main, "enqueue_import", lambda *args: calls.append(args))
    main.refresh_courses("alice")
    assert calls == []


def test_recent_history_is_bounded_and_chronological(seeded):
    for i in range(10):
        storage.append_exchange("alice", "math", str(i), {"content": str(i), "sources": []})
    messages = storage.recent_history("alice", "math", 6)
    assert len(messages) == 6
    assert messages[0]["content"] == "7" and messages[-1]["content"] == "9"


def test_history_api_is_bounded_with_full_export_preserved(seeded):
    from fastapi.testclient import TestClient
    for i in range(15):
        storage.append_exchange("alice", "math", str(i), {"content": str(i), "sources": []})
    storage.append_exchange("bob", "math", "private", {"content": "private"})
    headers = {"Authorization": "Bearer " + storage.create_session("alice")}
    with TestClient(main.app) as client:
        recent = client.get("/courses/math/history?limit=4", headers=headers).json()
        assert len(recent) == 4 and recent[0]["content"] == "13"
        assert len(client.get("/courses/math/history", headers=headers).json()) == 30
        assert client.get("/courses/math/history?limit=10000", headers=headers).status_code == 422
