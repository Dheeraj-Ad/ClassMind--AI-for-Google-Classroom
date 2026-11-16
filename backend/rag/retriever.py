from langchain_core.documents import Document

from backend.config import settings
from backend.rag.vectorstore import encode_query, open_collection
from backend.storage import manifest


def retrieve(user_id, course_id, query):
    current = manifest(user_id, course_id)
    if not current.get("collection"):
        return []
    collection = open_collection(current["collection"])
    count = collection.count()
    if not count:
        return []
    result = collection.query(query_embeddings=[encode_query(query)], n_results=min(count, settings().retrieval_k * 16),
                              where={"$and": [{"user_id": user_id}, {"course_id": course_id}]},
                              include=["metadatas", "distances"])
    parents = {}
    for metadata, distance in zip(result["metadatas"][0], result["distances"][0]):
        score = 1 - distance
        if score < settings().min_similarity or metadata["parent_id"] in parents:
            continue
        parents[metadata["parent_id"]] = Document(page_content=metadata["parent_text"],
                                                  metadata={**metadata, "similarity": round(score, 4)})
        if len(parents) == min(12, settings().retrieval_k * 2):
            break
    return list(parents.values())
