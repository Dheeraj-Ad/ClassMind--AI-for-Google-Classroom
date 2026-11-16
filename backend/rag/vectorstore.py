"""One persistent collection per user/course generation, with local embeddings."""
from functools import lru_cache
import threading
import uuid

import chromadb
from chromadb.config import Settings as ChromaSettings

from backend.config import settings
from backend.storage import digest

_model_lock = threading.RLock()


@lru_cache
def client():
    return chromadb.PersistentClient(path=str(settings().storage / "chroma"),
                                    settings=ChromaSettings(anonymized_telemetry=False))


@lru_cache
def embedding_model():
    from backend.rag.local_embeddings import LocalEmbeddingModel
    return LocalEmbeddingModel()


def encode(texts):
    with _model_lock:
        return embedding_model().encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()


@lru_cache(maxsize=128)
def encode_query(query):
    return encode([query])[0]


def collection_prefix(user_id, course_id):
    return "cm_" + digest(user_id + ":" + course_id)[:32] + "_"


def create_collection(user_id, course_id):
    name = collection_prefix(user_id, course_id) + uuid.uuid4().hex[:12]
    return client().create_collection(name, metadata={"hnsw:space": "cosine"}, embedding_function=None)


def open_collection(name):
    return client().get_collection(name, embedding_function=None)


def delete_collection(name):
    if name:
        from chromadb.errors import NotFoundError
        try:
            client().delete_collection(name)
        except NotFoundError:
            pass


def delete_course_collections(user_id, course_id, keep=None):
    prefix = collection_prefix(user_id, course_id)
    for collection in client().list_collections():
        name = collection if isinstance(collection, str) else collection.name
        if name.startswith(prefix) and name != keep:
            delete_collection(name)


def embedding_windows(text):
    """MiniLM's 256-wordpiece context must not truncate a 900-token parent chunk."""
    with _model_lock:
        model = embedding_model()
    ids = model.tokenizer.encode(text, add_special_tokens=False, truncation=False, verbose=False)
    width = min(224, model.max_seq_length - 2)
    step = width - 32
    for start in range(0, len(ids), step):
        yield model.tokenizer.decode(ids[start:start + width], skip_special_tokens=True)
        if start + width >= len(ids):
            break


def add_chunks(collection, chunks):
    records = []
    for chunk in chunks:
        parent = digest(chunk.metadata["file_id"] + ":" + str(chunk.metadata["page"]) + ":" + str(chunk.metadata["chunk_index"]))
        for i, window in enumerate(embedding_windows(chunk.page_content)):
            records.append((parent + "_" + str(i), window,
                            {**chunk.metadata, "parent_id": parent, "parent_text": chunk.page_content}))
            if len(records) >= 64:
                _write_batch(collection, records)
                records = []
    if records:
        _write_batch(collection, records)


def _write_batch(collection, records):
    ids, texts, metadatas = zip(*records)
    collection.add(ids=list(ids), documents=list(texts), metadatas=list(metadatas), embeddings=encode(list(texts)))


def copy_file_chunks(old_name, target, file_id, metadata_updates):
    """Copy already-computed vectors in bounded pages; no embedding inference."""
    source = open_collection(old_name)
    offset = 0
    while True:
        batch = source.get(where={"file_id": file_id}, limit=128, offset=offset,
                           include=["documents", "metadatas", "embeddings"])
        if not batch["ids"]:
            break
        target.add(ids=batch["ids"], documents=batch["documents"], embeddings=batch["embeddings"],
                   metadatas=[{**m, **metadata_updates} for m in batch["metadatas"]])
        offset += len(batch["ids"])
    return offset
