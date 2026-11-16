import json
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest

from backend.classroom import fetch_materials as materials_module
from backend.classroom import fetch_courses as courses_module
from backend.rag import ingest
from backend.storage import db, manifest, save_manifest
from backend.utils.errors import ServiceError
from backend.utils.file_processor import load_documents


class Resource:
    def __init__(self, pages):
        self.pages, self.calls = pages, []
    def list(self, **kwargs):
        self.calls.append(kwargs)
        value = self.pages[len(self.calls) - 1]
        return SimpleNamespace(execute=lambda **_: value)


def test_courses_are_paginated_active_student_only(seeded, monkeypatch):
    resource = Resource([{"courses": [{"id": "1", "name": "A"}], "nextPageToken": "p2"},
                         {"courses": [{"id": "2", "name": "B"}]}])
    monkeypatch.setattr(courses_module, "service", lambda *_: SimpleNamespace(courses=lambda: resource))
    assert len(courses_module.fetch_courses("alice")) == 2
    assert resource.calls[0]["studentId"] == "me"
    assert resource.calls[0]["courseStates"] == ["ACTIVE"]
    assert resource.calls[1]["pageToken"] == "p2"


def test_discovery_deduplicates_drive_files_and_reports_non_drive(monkeypatch):
    from backend.auth.google_auth import SCOPES
    monkeypatch.setattr(materials_module, "get_credentials", lambda _: SimpleNamespace(granted_scopes=SCOPES, scopes=SCOPES))
    attachment = {"driveFile": {"driveFile": {"id": "file", "title": "Notes"}}}
    materials = Resource([{"courseWorkMaterial": [{"id": "m", "title": "Topic", "materials": [attachment, {"link": {"url": "https://example.com"}}]}], "nextPageToken": "next"},
                          {"courseWorkMaterial": []}])
    announcements = Resource([{"announcements": [{"id": "a", "materials": [attachment]}]}])
    assignments = Resource([{"courseWork": []}])
    course = SimpleNamespace(courseWorkMaterials=lambda: materials, announcements=lambda: announcements, courseWork=lambda: assignments)
    monkeypatch.setattr(materials_module, "service", lambda *_: SimpleNamespace(courses=lambda: course))
    files, skipped, warnings = materials_module.fetch_materials("alice", "math")
    assert len(files) == 2 and files[0]["id"] == "file"
    assert files[1]["source_type"] == "web"
    assert not skipped and not warnings
    assert len(materials.calls) == 2


def test_discovery_without_assignment_permission_still_imports_materials(monkeypatch):
    from backend.auth.google_auth import REQUIRED_SCOPES
    monkeypatch.setattr(materials_module, "get_credentials", lambda _: SimpleNamespace(granted_scopes=None, scopes=REQUIRED_SCOPES))
    attachment = {"driveFile": {"driveFile": {"id": "file", "title": "Notes"}}}
    materials = Resource([{"courseWorkMaterial": [{"id": "post", "title": "Week 1", "materials": [attachment]}]}])
    announcements = Resource([{"announcements": []}])
    def forbidden():
        pytest.fail("Assignments must not be requested without permission")
    course = SimpleNamespace(courseWorkMaterials=lambda: materials, announcements=lambda: announcements, courseWork=forbidden)
    monkeypatch.setattr(materials_module, "service", lambda *_: SimpleNamespace(courses=lambda: course))
    files, skipped, warnings = materials_module.fetch_materials("alice", "math")
    assert [file["id"] for file in files] == ["file"]
    assert skipped[0]["id"] == "assignment-access-not-granted"
    assert warnings == []


def test_discovery_imports_post_text_and_deduplicates_youtube(monkeypatch):
    from backend.auth.google_auth import SCOPES
    monkeypatch.setattr(materials_module, "get_credentials", lambda _: SimpleNamespace(granted_scopes=SCOPES))
    post = {"id": "lesson", "title": "Neural networks", "description": "Watch this lesson before class: https://youtu.be/aircAruvnKk",
            "materials": [{"youtubeVideo": {"id": "aircAruvnKk", "title": "Neural networks"}}]}
    resource = Resource([{"courseWorkMaterial": [post]}])
    course = SimpleNamespace(courseWorkMaterials=lambda: resource, announcements=lambda: Resource([{}]), courseWork=lambda: Resource([{}]))
    monkeypatch.setattr(materials_module, "service", lambda *_: SimpleNamespace(courses=lambda: course))
    files, skipped, warnings = materials_module.fetch_materials("alice", "math")
    assert [f["source_type"] for f in files] == ["post", "youtube"]
    assert not skipped and not warnings


def test_pdf_extraction_keeps_page_numbers_and_warns_on_blank_pages(tmp_path):
    path = tmp_path / "notes.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Gradient descent updates weights to reduce the loss function.")
        pdf.new_page()
        pdf.save(path)
    documents, warnings = load_documents(path, {"course_id": "math"})
    assert documents[0].metadata["page"] == 1
    assert "Gradient descent" in documents[0].page_content
    assert warnings


def test_empty_scan_is_not_silently_indexed(tmp_path):
    path = tmp_path / "scan.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(path)
    with pytest.raises(ServiceError, match="No usable text"):
        load_documents(path, {})


def test_failed_discovery_preserves_previous_index(seeded, monkeypatch):
    old = {"collection": "previous", "files": [], "chunks": 9}
    save_manifest("alice", "math", old)
    with db() as con:
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", ("job", "alice", "math", "queued", "{}"))
    monkeypatch.setattr(ingest, "fetch_materials", lambda *_: ([], [], ["Google permission denied"]))
    monkeypatch.setattr(ingest, "create_collection", lambda *_: pytest.fail("Must not publish partial listings"))
    ingest.ingest_course("alice", "math", "job")
    assert manifest("alice", "math") == old
    with db() as con:
        assert con.execute("SELECT status FROM jobs WHERE id='job'").fetchone()[0] == "failed"


def test_cleanup_refuses_root_or_outside_paths(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    with pytest.raises(ValueError):
        ingest.safe_remove_tree(root, root)
    with pytest.raises(ValueError):
        ingest.safe_remove_tree(root, tmp_path)


def test_successful_resync_publishes_new_manifest_and_drops_removed_file(seeded, monkeypatch):
    from langchain_core.documents import Document
    old = {"collection": "old", "files": [{"id": "removed", "status": "imported"}], "chunks": 4}
    save_manifest("alice", "math", old)
    staged = SimpleNamespace(name="new", count=lambda: 2)
    monkeypatch.setattr(ingest, "create_collection", lambda *_: staged)
    monkeypatch.setattr(ingest, "fetch_materials", lambda *_: ([{"id": "kept", "file_name": "notes.txt", "material_title": "Week 1"}], [], []))
    monkeypatch.setattr(ingest, "service", lambda *_: object())
    monkeypatch.setattr(ingest, "download_file", lambda *_: (Path("mock.txt"), {"name": "notes.txt"}))
    monkeypatch.setattr(ingest, "load_documents", lambda p, m: ([Document(page_content="A real course fact with enough text.", metadata={**m, "page": 1})], []))
    monkeypatch.setattr(ingest, "split_documents", lambda d: d)
    monkeypatch.setattr(ingest, "add_chunks", lambda *_: None)
    monkeypatch.setattr(ingest, "extract_topics", lambda *_: (["course fact"], None))
    cleaned = []
    monkeypatch.setattr(ingest, "delete_course_collections", lambda u, c, keep: cleaned.append(keep))
    with db() as con:
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", ("job", "alice", "math", "queued", "{}"))
    ingest.ingest_course("alice", "math", "job")
    current = manifest("alice", "math")
    assert current["collection"] == "new"
    assert [f["id"] for f in current["files"]] == ["kept"]
    assert current["files"][0]["status"] == "imported"
    assert cleaned == ["new"]


def test_index_failure_retains_old_manifest_and_removes_stage(seeded, monkeypatch):
    from langchain_core.documents import Document
    old = {"collection": "old", "files": [], "chunks": 4}
    save_manifest("alice", "math", old)
    monkeypatch.setattr(ingest, "create_collection", lambda *_: SimpleNamespace(name="stage"))
    monkeypatch.setattr(ingest, "fetch_materials", lambda *_: ([{"id": "file", "file_name": "notes.txt", "material_title": "Week 1"}], [], []))
    monkeypatch.setattr(ingest, "service", lambda *_: object())
    monkeypatch.setattr(ingest, "download_file", lambda *_: (Path("mock.txt"), {"name": "notes.txt"}))
    monkeypatch.setattr(ingest, "load_documents", lambda *_: ([Document(page_content="fact")], []))
    monkeypatch.setattr(ingest, "split_documents", lambda d: d)
    def fail(*_):
        raise RuntimeError("disk full")
    monkeypatch.setattr(ingest, "add_chunks", fail)
    deleted = []
    monkeypatch.setattr(ingest, "delete_collection", deleted.append)
    with db() as con:
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", ("job", "alice", "math", "queued", "{}"))
    ingest.ingest_course("alice", "math", "job")
    assert manifest("alice", "math") == old
    assert deleted == ["stage"]


@pytest.mark.parametrize("download_allowed", [True, False])
def test_incremental_reuse_checks_permission_and_skips_inference(seeded, monkeypatch, download_allowed):
    from backend.storage import digest
    run = "a" * 16
    basename = digest("file")[:32]
    previous = {"collection": "old", "run_id": run, "index_signature": ingest.index_signature()}
    raw_dir = ingest.course_directory("raw", "alice", "math") / run
    proc_dir = ingest.course_directory("processed", "alice", "math") / run
    raw_dir.mkdir(parents=True)
    proc_dir.mkdir(parents=True)
    (raw_dir / (basename + ".txt")).write_text("Gradient descent reduces training loss.", encoding="utf-8")
    (proc_dir / (basename + ".jsonl")).write_text(json.dumps({"text": "Gradient descent reduces training loss.", "metadata": {"page": 1}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(ingest, "file_info", lambda *_: {"name": "Renamed.txt", "modifiedTime": "unchanged",
                        "mimeType": "text/plain", "capabilities": {"canDownload": download_allowed}})
    calls = []
    monkeypatch.setattr(ingest, "copy_file_chunks", lambda *args: calls.append(args) or 1)
    monkeypatch.setattr(ingest, "add_chunks", lambda *_: pytest.fail("No new embeddings for an unchanged file"))
    new_raw, new_proc = raw_dir.parent / ("b" * 16), proc_dir.parent / ("b" * 16)
    new_proc.mkdir()
    args = ("alice", "math", {"name": "Math"}, {"id": "file", "material_title": "Week 1"},
            {"id": "file", "modified_time": "unchanged", "chunks": 1, "warnings": []}, previous,
            object(), object(), new_raw, new_proc)
    if download_allowed:
        report = ingest.reuse_file(*args)
        assert report["reused"] is True and report["file_name"] == "Renamed.txt"
        assert len(calls) == 1 and (new_raw / (basename + ".txt")).exists()
    else:
        with pytest.raises(ServiceError, match="disabled"):
            ingest.reuse_file(*args)
        assert calls == []


def test_youtube_override_import_does_not_expose_transcript_in_report(seeded, monkeypatch):
    from backend.storage import save_extra_source
    item = {"id": "youtube:aircAruvnKk", "video_id": "aircAruvnKk", "source_type": "youtube", "file_name": "Qubits",
            "material_title": "Quantum lesson", "source_url": "https://www.youtube.com/watch?v=aircAruvnKk"}
    text = "A qubit is the basic unit of quantum information."
    save_extra_source("alice", "math", {**item, "transcript": text})
    monkeypatch.setattr(ingest, "fetch_materials", lambda *_: ([item], [], []))
    monkeypatch.setattr(ingest, "service", lambda *_: pytest.fail("Transcript does not require Drive"))
    monkeypatch.setattr(ingest, "create_collection", lambda *_: SimpleNamespace(name="new", count=lambda: 1))
    monkeypatch.setattr(ingest, "split_documents", lambda d: d)
    indexed = []
    monkeypatch.setattr(ingest, "add_chunks", lambda _, chunks: indexed.extend(chunks))
    monkeypatch.setattr(ingest, "delete_course_collections", lambda *_a, **_k: None)
    with db() as con:
        con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)", ("video-job", "alice", "math", "queued", "{}"))
    ingest.ingest_course("alice", "math", "video-job")
    report = manifest("alice", "math")["files"][0]
    assert report["status"] == "imported" and "transcript" not in report
    assert indexed[0].page_content == text
