import pytest
from langchain_core.documents import Document

from backend.rag import chain


@pytest.fixture(autouse=True)
def isolated_reranker(monkeypatch):
    monkeypatch.setattr(chain, "semantic_scores", lambda query, candidates: [0.8] * len(candidates))


def document(text="Gradient descent updates weights to reduce the loss function."):
    return Document(page_content=text, metadata={"file_name": "Lecture.pdf", "file_id": "drive-1", "page": 2,
                                                "material_title": "Week 1", "similarity": 0.8})


def test_local_answer_returns_only_verbatim_evidence_without_network(monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", lambda *_a, **_k: pytest.fail("No external API allowed"))
    doc = document()
    monkeypatch.setattr(chain, "retrieve", lambda *_: [doc])
    result = chain.answer_question("alice", "math", "Explain gradient descent")
    assert result["grounded"]
    assert result["answer_mode"] == "local_evidence"
    assert doc.page_content in result["content"]
    assert result["sources"][0]["quotes"] == [doc.page_content]
    assert result["sources"][0]["page"] == 2


@pytest.mark.parametrize("docs", [[], [document()]])
def test_unrelated_request_abstains_even_if_retriever_returns_nearby_topic(monkeypatch, docs):
    monkeypatch.setattr(chain, "retrieve", lambda *_: docs)
    result = chain.answer_question("alice", "math", "How do zebras sleep?")
    assert result["content"] == chain.ABSTENTION
    assert result["sources"] == []


def test_followup_uses_previous_user_question_only():
    history = [{"role": "user", "content": "Explain gradient descent"},
               {"role": "assistant", "content": "Untrusted assistant statement"}]
    assert "gradient descent" in chain.resolve_query("Explain it in more detail", history)
    assert "Untrusted" not in chain.resolve_query("Explain it in more detail", history)
    assert chain.resolve_query("What is photosynthesis?", history) == "What is photosynthesis?"


def test_local_study_recommendation_keeps_source_page(monkeypatch):
    monkeypatch.setattr(chain, "retrieve", lambda *_: [document()])
    result = chain.answer_question("alice", "math", "What should I study for gradient descent?", mode="study")
    assert "Read Lecture.pdf, page 2" in result["content"]
    assert "[S1]" in result["content"]


def test_practice_cards_have_answers_from_the_cited_excerpt(monkeypatch):
    doc = document()
    monkeypatch.setattr(chain, "retrieve", lambda *_: [doc])
    result = chain.answer_question("alice", "math", "Generate practice questions about gradient descent", mode="practice")
    assert "_____" in result["content"]
    answer = result["content"].split("Answer: ")[1].split(" [S1]")[0]
    assert answer in doc.page_content


def test_topic_labels_are_local_and_occur_in_source():
    text = "Gradient descent minimizes training loss. Gradient descent is an optimization technique."
    tags, warning = chain.extract_topics(text)
    assert tags and warning is None
    assert all(tag in text for tag in tags)


def test_long_passages_remain_exact_substrings():
    original = "Gradient descent " * 100 + "reduces loss."
    passages = list(chain.passages(original))
    assert len(passages) > 1
    assert all(passage in original and len(passage) <= 900 for passage in passages)


def test_syllabus_topic_list_is_not_an_explanation(monkeypatch):
    doc = document("Week 14: Physical realizations of qubits, quantum gates, quantum computing and algorithms.")
    monkeypatch.setattr(chain, "retrieve", lambda *_: [doc])
    result = chain.answer_question("alice", "math", "What are qubits?")
    assert not result["grounded"] and not result["sources"]


def test_clear_definition_beats_syllabus_and_preserves_timestamp(monkeypatch):
    doc = document("A qubit is the basic unit of quantum information.")
    doc.metadata.update(source_type="youtube", timestamp=42, source_url="https://www.youtube.com/watch?v=aircAruvnKk&t=42s")
    monkeypatch.setattr(chain, "retrieve", lambda *_: [document("Week 14: physical realizations of qubits and quantum computing."), doc])
    result = chain.answer_question("alice", "math", "What are qubits?")
    assert result["answer"] == doc.page_content
    assert result["sources"][0]["timestamp"] == 42


def test_disadvantages_do_not_return_unrelated_definition(monkeypatch):
    monkeypatch.setattr(chain, "retrieve", lambda *_: [document()])
    result = chain.answer_question("alice", "math", "What are the disadvantages of gradient descent?")
    assert not result["grounded"]


def test_course_info_reads_actual_course_metadata(seeded, monkeypatch):
    monkeypatch.setattr(chain, "retrieve", lambda *_: pytest.fail("Course info does not need embedding search"))
    result = chain.answer_question("alice", "math", "Tell me course info")
    assert result["answer"] == "Math"
    assert result["answer_mode"] == "course_overview"
    assert "0 imported sources" in result["content"]
