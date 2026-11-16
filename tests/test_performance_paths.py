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
