from pathlib import Path

from streamlit.testing.v1 import AppTest
import pytest


def test_logged_out_landing_page_renders():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.run(timeout=20)
    assert not app.exception
    assert any("A clearer way to study" in item.value for item in app.markdown)


def test_connected_dashboard_renders_course_and_progress(monkeypatch):
    import requests
    from types import SimpleNamespace
    course = {"id": "math", "name": "Applied Mathematics", "section": "Year 1", "documents": 2,
              "chunks": 10, "synced_at": None, "job": {"status": "running", "progress": 0.5, "message": "Importing notes"}}
    def request(method, url, **kwargs):
        result = {"name": "Test Student"} if url.endswith("/me") else [course]
        return SimpleNamespace(ok=True, json=lambda: result)
    monkeypatch.setattr(requests, "request", request)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state["token"] = "test-session"
    app.run(timeout=20)
    assert not app.exception
    assert any("Applied Mathematics" == subheader.value for subheader in app.subheader)
    assert len(app.metric) == 3


@pytest.mark.parametrize("page,title", [("course_chat", "Understand what you're studying"),
                                       ("materials_browser", "Everything you learn from")])
def test_connected_chat_and_library_render(monkeypatch, tmp_path, page, title):
    import requests
    from types import SimpleNamespace
    course = {"id": "math", "name": "Applied Mathematics", "section": "Year 1", "documents": 1,
              "chunks": 2, "synced_at": None, "job": None}
    source = {"id": "youtube:aircAruvnKk", "file_name": "Lesson video", "status": "imported",
              "source_type": "youtube", "source_url": "https://www.youtube.com/watch?v=aircAruvnKk", "chunks": 2}
    def request(method, url, **kwargs):
        if url.endswith("/me"):
            result = {"name": "Test Student"}
        elif url.endswith("/history"):
            result = [{"role": "assistant", "content": "A cited explanation.", "answer": "A cited explanation.",
                       "sources": [{"id": "S1", "file_id": source["id"], "file_name": "Lesson video", "source_type": "youtube",
                                    "timestamp": 42, "source_url": source["source_url"], "quotes": ["A cited explanation."]}]}]
        elif url.endswith("/materials"):
            result = {"files": [source], "chunks": 2}
        else:
            result = [course]
        return SimpleNamespace(ok=True, json=lambda: result)
    monkeypatch.setattr(requests, "request", request)
    code = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    code = code.replace("st.navigation([dashboard_page, chat_page, materials_page]).run()", page + "()")
    screen = tmp_path / "screen.py"
    screen.write_text(code, encoding="utf-8")
    app = AppTest.from_file(str(screen))
    app.session_state["token"] = "test-session"
    app.run(timeout=20)
    assert not app.exception
    assert any(item.value == title for item in app.title)
    if page == "course_chat":
        assert any("0:42 in video" in item.value for item in app.caption)
    else:
        assert len(app.tabs) == 3
        assert any(item.value == "Lesson video" for item in app.subheader)
