"""Read-only timing check against an existing course; prints no course text or IDs."""
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil
from backend.main import course_summaries
from backend.rag.chain import answer_question, extract_topics
from backend.rag.vectorstore import embedding_model, open_collection
from backend.storage import db


def main():
    result = {}
    with db() as con:
        row = con.execute("SELECT user_id,course_id,payload FROM manifests WHERE json_extract(payload,'$.chunks')>0 LIMIT 1").fetchone()
    if not row:
        print("Import one course first, then run this benchmark.")
        return
    t0 = time.perf_counter()
    embedding_model()
    result["model_load_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    payload = json.loads(row["payload"])
    sample = open_collection(payload["collection"]).get(limit=1, include=["metadatas"])
    excerpt = sample["metadatas"][0]["parent_text"]
    topics, _ = extract_topics(excerpt)
    query = "What should I study for " + (topics[0] if topics else " ".join(excerpt.split()[:8])) + "?"
    times = []
    for _ in range(3):
        start = time.perf_counter()
        answer = answer_question(row["user_id"], row["course_id"], query, mode="study")
        times.append(round((time.perf_counter() - start) * 1000, 1))
    result["local_answer_ms"] = times
    result["has_cited_evidence"] = bool(answer["sources"])
    dashboard_times = []
    for _ in range(3):
        start = time.perf_counter()
        courses = course_summaries(row["user_id"])
        dashboard_times.append(round((time.perf_counter() - start) * 1000, 1))
    result["dashboard_ms"] = dashboard_times
    result["dashboard_payload_kb"] = round(len(json.dumps(courses).encode()) / 1024, 1)
    result["process_rss_mb"] = round(psutil.Process().memory_info().rss / 1024**2, 1)
    result["ram_available_gb"] = round(psutil.virtual_memory().available / 1024**3, 2)
    result["torch_loaded"] = "torch" in sys.modules
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
