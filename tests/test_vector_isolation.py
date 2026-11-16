import pytest

from backend.rag import retriever, vectorstore
from backend.storage import save_manifest


def test_copy_reuses_vectors_without_model_calls(seeded, monkeypatch):
    vectorstore.client.cache_clear()
    source = vectorstore.create_collection("alice", "math")
    target = vectorstore.create_collection("alice", "math")
    source.add(ids=["cached"], embeddings=[[1.0, 0.0, 0.0]], documents=["Gradient descent reduces loss."],
               metadatas=[{"file_id": "f1", "file_name": "Old title", "page": 2}])
    monkeypatch.setattr(vectorstore, "encode", lambda *_: pytest.fail("Cached vectors must not be recomputed"))
    assert vectorstore.copy_file_chunks(source.name, target, "f1", {"file_name": "New title"}) == 1
    copied = target.get(include=["embeddings", "metadatas", "documents"])
    assert copied["metadatas"][0]["file_name"] == "New title"
    assert copied["metadatas"][0]["page"] == 2
    assert copied["embeddings"][0].tolist() == [1.0, 0.0, 0.0]
    vectorstore.delete_collection(source.name)
    vectorstore.delete_collection(target.name)
    vectorstore.client.cache_clear()


def test_real_chroma_separates_users_and_courses(seeded, monkeypatch):
    vectorstore.client.cache_clear()
    monkeypatch.setattr(retriever, "encode_query", lambda _: [1.0, 0.0, 0.0])
    created = []
    for user, course, secret in [("alice", "math", "algebra"), ("alice", "physics", "gravity"), ("bob", "math", "bob-private")]:
        collection = vectorstore.create_collection(user, course)
        created.append(collection.name)
        collection.add(ids=["one"], embeddings=[[1.0, 0.0, 0.0]], documents=[secret],
                       metadatas=[{"user_id": user, "course_id": course, "parent_id": "one", "parent_text": secret, "file_name": "test.txt"}])
        save_manifest(user, course, {"collection": collection.name, "files": [], "chunks": 1})
    assert len(set(created)) == 3
    assert [d.page_content for d in retriever.retrieve("alice", "math", "test")] == ["algebra"]
    assert [d.page_content for d in retriever.retrieve("bob", "math", "test")] == ["bob-private"]
    vectorstore.delete_course_collections("alice", "math")
    assert vectorstore.open_collection(created[1]).count() == 1
    assert vectorstore.open_collection(created[2]).count() == 1
    for name in created[1:]:
        vectorstore.delete_collection(name)
    vectorstore.client.cache_clear()
