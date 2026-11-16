"""Staged, repeatable course imports: publish only after a complete listing/index."""
import json
import logging
import os
import re
import shutil
import time
import traceback
import uuid
from pathlib import Path

from backend.classroom.download_files import download_file, file_info, validate_download
from backend.classroom.fetch_courses import require_course, service
from backend.classroom.fetch_materials import fetch_materials
from backend.classroom.load_sources import load_source
from backend.config import settings
from backend.rag.chain import extract_topics
from backend.rag.vectorstore import add_chunks, copy_file_chunks, create_collection, delete_collection, delete_course_collections
from backend.storage import db, digest, manifest, save_manifest
from backend.utils.errors import ServiceError
from backend.utils.file_processor import load_documents
from backend.utils.locks import course_lock

log = logging.getLogger(__name__)


def safe_remove_tree(root: Path, target: Path):
    root, target = root.resolve(), target.resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError("Refusing to remove a directory outside its data root")
    if target.exists():
        shutil.rmtree(target)


def course_directory(kind, user_id, course_id):
    # Keep paths short enough for typical Windows installations without long-path mode.
    return settings().storage / kind / digest(user_id)[:24] / digest(course_id)[:24]


def update_job(job_id, status, **payload):
    with db() as con:
        con.execute("UPDATE jobs SET status=?,payload=? WHERE id=?", (status, json.dumps(payload), job_id))


def split_documents(documents):
    os.environ.setdefault("USE_TORCH", "0")
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    config = settings()
    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(config.storage / "tokenizers"))
    splitter = RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base", chunk_size=config.chunk_tokens, chunk_overlap=config.chunk_overlap,
    )
    chunks = splitter.split_documents(documents)
    for i, chunk in enumerate(chunks):
        chunk.metadata["chunk_index"] = i
    return chunks


def index_signature():
    config = settings()
    return f"{config.embedding_model}:windows224:{config.chunk_tokens}:{config.chunk_overlap}"


def reuse_file(user_id, course_id, course, item, old_report, previous, drive, staged, raw, processed):
    if item.get("source_type", "drive") != "drive" or item.get("transcript"):
        return None
    if not old_report or not old_report.get("modified_time") or not previous.get("collection"):
        return None
    legacy = "sentence-transformers/all-MiniLM-L6-v2:windows224:900:150"
    if previous.get("index_signature", legacy) != index_signature():
        return None
    run_id = previous.get("run_id", "")
    if not re.fullmatch(r"[a-f0-9]{16,32}", run_id):
        return None
    basename = digest(item["id"])[:32]
    old_processed = course_directory("processed", user_id, course_id) / run_id / (basename + ".jsonl")
    old_raw_dir = course_directory("raw", user_id, course_id) / run_id
    old_raw = next((p for p in old_raw_dir.glob(basename + ".*") if p.suffix in {".pdf", ".docx", ".pptx", ".txt", ".xlsx", ".html", ".epub"}), None)
    if not old_processed.is_file() or old_raw is None:
        return None
    info = file_info(drive, item["id"])
    validate_download(info)  # Recheck access even when reusing cached vectors.
    if info.get("modifiedTime") != old_report["modified_time"]:
        return None
    updates = {"file_name": info["name"], "material_title": item["material_title"], "course_name": course["name"],
               "source_type": "drive", "source_url": item.get("source_url", "")}
    count = copy_file_chunks(previous["collection"], staged, item["id"], updates)
    if not count:
        return None
    raw.mkdir(parents=True, exist_ok=True)
    shutil.copy2(old_raw, raw / old_raw.name)
    sample = ""
    with old_processed.open(encoding="utf-8") as source, (processed / old_processed.name).open("w", encoding="utf-8") as output:
        for line in source:
            chunk = json.loads(line)
            chunk["metadata"].update(updates)
            output.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            if len(sample) < 30000:
                sample += chunk["text"][:30000 - len(sample)] + "\n"
    tags, _ = extract_topics(sample)
    return {**old_report, **item, "file_name": info["name"], "status": "imported", "reused": True, "topics": tags,
            "warnings": [w for w in old_report.get("warnings", []) if not w.startswith("Topic")]}


def ingest_course(user_id, course_id, job_id):
    with course_lock(user_id, course_id):
        staged = None
        published = False
        run_id = uuid.uuid4().hex[:16]
        raw = course_directory("raw", user_id, course_id) / run_id
        processed = course_directory("processed", user_id, course_id) / run_id
        try:
            course = require_course(user_id, course_id)
            update_job(job_id, "running", progress=0.02, message="Discovering Classroom attachments")
            files, skipped, warnings = fetch_materials(user_id, course_id)
            from backend.storage import extra_sources
            combined = {item["id"]: item for item in files}
            combined.update({item["id"]: item for item in extra_sources(user_id, course_id)})
            files = list(combined.values())
            if warnings:
                # Never silently erase a previous index because one source failed to list.
                raise ServiceError("Import stopped; previous index retained. " + " ".join(warnings), 503)
            staged = create_collection(user_id, course_id)
            previous = manifest(user_id, course_id)
            old_files = {f["id"]: f for f in previous["files"] if f.get("status") == "imported"}
            drive = service(user_id, "drive", "v3") if any(f.get("source_type", "drive") == "drive" and not f.get("transcript") for f in files) else None
            reports, total_chunks = list(skipped), 0
            processed.mkdir(parents=True, exist_ok=True)
            for i, item in enumerate(files):
                report = {k: v for k, v in item.items() if k not in {"text", "transcript"}}
                report.update(status="importing", topics=[], warnings=[], chunks=0)
                update_job(job_id, "running", progress=0.05 + 0.9 * i / max(1, len(files)),
                           message=f"Importing {i + 1}/{len(files)}: {item['file_name']}", files=reports)
                try:
                    cached = reuse_file(user_id, course_id, course, item, old_files.get(item["id"]), previous,
                                        drive, staged, raw, processed)
                except ServiceError as exc:
                    if exc.status_code in {401, 429, 503}:
                        raise
                    report.update(status="skipped", reason=exc.message)
                    reports.append(report)
                    continue
                if cached:
                    total_chunks += cached.get("chunks", 0)
                    reports.append(cached)
                    continue
                try:
                    kind = item.get("source_type", "drive")
                    metadata = {"user_id": user_id, "course_id": course_id, "course_name": course["name"],
                                "file_id": item["id"], "file_name": item["file_name"], "material_title": item["material_title"],
                                "source_type": kind, "source_url": item.get("source_url", "")}
                    if kind == "drive" and not item.get("transcript"):
                        path, info = download_file(drive, item["id"], raw)
                        report["file_name"] = info["name"]
                        report["modified_time"] = info.get("modifiedTime", "")
                        metadata["file_name"] = info["name"]
                        documents, extraction_warnings = load_documents(path, metadata)
                    else:
                        documents, extraction_warnings = load_source(item, raw, metadata)
                    chunks = split_documents(documents)
                    if not chunks:
                        raise ServiceError("No text chunks could be extracted.")
                except ServiceError as exc:
                    if exc.status_code in {401, 429, 503} and item.get("source_type", "drive") == "drive":
                        raise  # Retryable provider outage: do not publish an incomplete replacement.
                    report.update(status="skipped", reason=exc.message)
                    reports.append(report)
                    continue
                except Exception:
                    report.update(status="failed", reason="Document extraction failed. The file may be malformed or required NLP data may be missing.")
                    reports.append(report)
                    continue
                # Indexing failures abort the entire staged index, never leave half a file indexed.
                update_job(job_id, "running", progress=0.05 + 0.9 * i / max(1, len(files)),
                           message=f"Indexing {i + 1}/{len(files)} locally ({len(chunks)} chunks)", files=reports)
                add_chunks(staged, chunks)
                tags, topic_warning = extract_topics("\n".join(d.page_content for d in documents))
                with (processed / (digest(item["id"])[:32] + ".jsonl")).open("w", encoding="utf-8") as output:
                    for chunk in chunks:
                        output.write(json.dumps({"text": chunk.page_content, "metadata": chunk.metadata}, ensure_ascii=False) + "\n")
                report.update(status="imported", chunks=len(chunks), topics=tags,
                              warnings=extraction_warnings + ([topic_warning] if topic_warning else []))
                total_chunks += len(chunks)
                reports.append(report)
            value = {"collection": staged.name, "files": reports, "chunks": total_chunks,
                     "embedding_windows": staged.count(), "synced_at": time.time(), "run_id": run_id,
                     "index_signature": index_signature()}
            save_manifest(user_id, course_id, value)
            published = True
            update_job(job_id, "completed", progress=1.0, message="Import complete", files=reports,
                       documents=sum(r["status"] == "imported" for r in reports), chunks=total_chunks)
            try:
                delete_course_collections(user_id, course_id, keep=staged.name)
                for kind in ("raw", "processed"):
                    parent = course_directory(kind, user_id, course_id)
                    if parent.exists():
                        for old in parent.iterdir():
                            if old.is_dir() and old.name != run_id:
                                safe_remove_tree(settings().storage / kind, old)
            except Exception:
                log.warning("Old import cleanup failed; the new index is usable.")
        except Exception as exc:
            location = traceback.extract_tb(exc.__traceback__)[-1]
            log.error("Course import failed: %s at %s:%s", type(exc).__name__, Path(location.filename).name, location.lineno)
            message = exc.message if isinstance(exc, ServiceError) else "Import failed. Check model availability, disk space and extraction dependencies; previous index retained."
            update_job(job_id, "failed", progress=0, message=message)
            if not published:
                try:
                    if staged is not None:
                        delete_collection(staged.name)
                    safe_remove_tree(settings().storage / "raw", raw)
                    safe_remove_tree(settings().storage / "processed", processed)
                except Exception:
                    log.warning("Staged import cleanup failed; re-sync or delete the knowledge base to clean up.")


def delete_knowledge_base(user_id, course_id):
    with course_lock(user_id, course_id):
        with db() as con:
            active = con.execute("SELECT 1 FROM jobs WHERE user_id=? AND course_id=? AND status IN ('queued','running')",
                                 (user_id, course_id)).fetchone()
        if active:
            raise ServiceError("Wait for the active import to finish before deleting this course.", 409)
        delete_course_collections(user_id, course_id)
        for kind in ("raw", "processed"):
            safe_remove_tree(settings().storage / kind, course_directory(kind, user_id, course_id))
        with db() as con:
            con.execute("DELETE FROM manifests WHERE user_id=? AND course_id=?", (user_id, course_id))
            con.execute("DELETE FROM messages WHERE user_id=? AND course_id=?", (user_id, course_id))
            con.execute("DELETE FROM jobs WHERE user_id=? AND course_id=?", (user_id, course_id))
            con.execute("DELETE FROM extra_sources WHERE user_id=? AND course_id=?", (user_id, course_id))
