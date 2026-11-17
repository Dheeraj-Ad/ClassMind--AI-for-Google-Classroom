import json
from types import SimpleNamespace

import pytest
import requests
from langchain_core.documents import Document

from backend.config import settings
from backend.rag import chain, gemini

TEXT = "Gradient descent updates model weights to reduce the loss function."


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("ANSWER_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-a-real-credential")
    settings.cache_clear()
    yield
    settings.cache_clear()


def document(text=TEXT):
    return Document(page_content=text, metadata={"file_name": "Lesson.pdf", "file_id": "private-drive-id",
        "user_id": "private-user-id", "course_id": "private-course-id", "page": 3})


def payload(sid="S1", quote=TEXT):
    return {"supported": True, "answer": {"text": "It adjusts the model's weights to reduce loss.",
            "citations": [{"source_id": sid, "quote": quote}]}, "details": []}


def fake_response(value=None, status=200, finish="STOP"):
    return SimpleNamespace(ok=status == 200, status_code=status,
        json=lambda: {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": json.dumps(value or payload())}]}}]})


def mock_provider(monkeypatch, result):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(gemini, "session", lambda: SimpleNamespace(post=post))
    return calls


def test_one_bounded_request_sends_no_identity_metadata(enabled, monkeypatch):
    calls = mock_provider(monkeypatch, fake_response())
    result = gemini.generate_answer("Explain gradient descent", "gradient descent", [document()] * 10, "chat")
    assert result.supported and len(calls) == 1
    url, kwargs = calls[0]
    assert "test-key" not in url and kwargs["allow_redirects"] is False
    assert kwargs["headers"]["x-goog-api-key"] == "test-key-not-a-real-credential"
    assert kwargs["timeout"] == (4, 20)
    body = kwargs["json"]
    sent = json.loads(body["contents"][0]["parts"][0]["text"])
    assert len(sent["sources"]) == 4
    assert "private-user-id" not in json.dumps(body) and "private-drive-id" not in json.dumps(body)
    assert "Lesson.pdf" not in json.dumps(body)


@pytest.mark.parametrize("value", [payload("S4"), payload(quote="This quotation does not occur in the provided evidence."),
    {"supported": True, "answer": {"text": "Unsupported answer", "citations": []}, "details": []}])
def test_fabricated_or_missing_references_are_rejected(enabled, monkeypatch, value):
    mock_provider(monkeypatch, fake_response(value))
    with pytest.raises(gemini.GeminiUnavailable, match="references"):
        gemini.generate_answer("Question", "Question", [document()], "chat")


@pytest.mark.parametrize("status,message", [(401, "authentication"), (403, "authentication"), (429, "quota"), (503, "could not complete")])
def test_provider_errors_are_sanitized_without_retry(enabled, monkeypatch, status, message):
    calls = mock_provider(monkeypatch, fake_response({"secret": "must not appear"}, status=status))
    with pytest.raises(gemini.GeminiUnavailable, match=message) as error:
        gemini.generate_answer("Question", "Question", [document()], "chat")
    assert "must not appear" not in str(error.value) and len(calls) == 1


def test_timeout_uses_local_answer_with_visible_notice(enabled, monkeypatch):
    calls = mock_provider(monkeypatch, requests.Timeout())
    monkeypatch.setattr(chain, "retrieve", lambda *_: [document()])
    monkeypatch.setattr(chain, "semantic_scores", lambda q, c: [0.9] * len(c))
    result = chain.answer_question("alice", "math", "Explain gradient descent")
    assert result["answer_mode"] == "local_evidence" and result["grounded"]
    assert "took too long" in result["provider_notice"] and len(calls) == 1


def test_cited_generation_keeps_actual_source_metadata(enabled, monkeypatch):
    mock_provider(monkeypatch, fake_response(payload("S2")))
    monkeypatch.setattr(chain, "retrieve", lambda *_: [document("Unrelated passage."), document()])
    result = chain.answer_question("alice", "math", "Explain gradient descent")
    assert result["answer_mode"] == "gemini" and result["answer_source_ids"] == ["S2"]
    assert result["sources"][0]["page"] == 3 and result["sources"][0]["quotes"] == [TEXT]
    assert result["answer"] == payload()["answer"]["text"]


def test_empty_retrieval_never_calls_google(enabled, monkeypatch):
    monkeypatch.setattr(chain, "retrieve", lambda *_: [])
    monkeypatch.setattr(gemini, "generate_answer", lambda *_: pytest.fail("No sources must not call Gemini"))
    assert not chain.answer_question("alice", "math", "What are qubits?")["grounded"]


def test_model_abstention_is_respected(enabled, monkeypatch):
    value = {"supported": False, "answer": {"text": "", "citations": []}, "details": []}
    mock_provider(monkeypatch, fake_response(value))
    monkeypatch.setattr(chain, "retrieve", lambda *_: [document()])
    result = chain.answer_question("alice", "math", "What are qubits?")
    assert not result["grounded"] and not result["sources"] and result["answer_mode"] == "gemini"


def test_partial_response_is_not_shown(enabled, monkeypatch):
    mock_provider(monkeypatch, fake_response(finish="MAX_TOKENS"))
    with pytest.raises(gemini.GeminiUnavailable):
        gemini.generate_answer("Question", "Question", [document()], "chat")


def test_health_and_configuration_do_not_expose_key(enabled):
    from backend.main import health
    assert health()["answer_mode"] == "gemini" and health()["gemini_key_configured"]
    assert "test-key-not-a-real-credential" not in json.dumps(health())
    assert "test-key-not-a-real-credential" not in str(settings())
    assert "gemini_api_key" not in settings().model_dump()
