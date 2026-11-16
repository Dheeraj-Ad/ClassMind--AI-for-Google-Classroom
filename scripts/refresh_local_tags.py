"""Regenerate old Groq-dependent tags locally, without downloading or reindexing."""
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.rag.chain import extract_topics
from backend.rag.ingest import course_directory
from backend.storage import db, digest


def refresh_tags():
    updated, files = 0, 0
    with db() as con:
        rows = con.execute("SELECT user_id,course_id,payload FROM manifests").fetchall()
    for row in rows:
        value = json.loads(row["payload"])
        run = value.get("run_id", "")
        if not re.fullmatch(r"[a-f0-9]{16,32}", run):
            continue
        changed = False
        for report in value.get("files", []):
            if report.get("status") != "imported":
                continue
            path = course_directory("processed", row["user_id"], row["course_id"]) / run / (digest(report["id"])[:32] + ".jsonl")
            if not path.is_file():
                continue
            sample = ""
            with path.open(encoding="utf-8") as source:
                for line in source:
                    sample += json.loads(line)["text"][:30000 - len(sample)]
                    if len(sample) >= 30000:
                        break
            report["topics"], _ = extract_topics(sample)
            report["warnings"] = [w for w in report.get("warnings", []) if not w.startswith("Topic")]
            changed = True
            files += 1
        if changed:
            with db() as con:
                cursor = con.execute("UPDATE manifests SET payload=? WHERE user_id=? AND course_id=? AND payload=?",
                                     (json.dumps(value), row["user_id"], row["course_id"], row["payload"]))
                updated += cursor.rowcount
    print(f"Local topic tags refreshed: {files} files across {updated} course indexes. No AI API calls.")


if __name__ == "__main__":
    refresh_tags()
