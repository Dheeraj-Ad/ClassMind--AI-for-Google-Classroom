"""Source readers, size limits, network boundaries and user isolation."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.classroom import download_files, load_sources, public_web
from backend.classroom.source_types import link_source, youtube_id
from backend.utils.errors import ServiceError
from backend.utils.file_processor import load_documents


@pytest.mark.parametrize("url", ["https://youtu.be/aircAruvnKk?t=2", "https://www.youtube.com/watch?v=aircAruvnKk",
    "https://m.youtube.com/shorts/aircAruvnKk", "https://www.youtube-nocookie.com/embed/aircAruvnKk"])
def test_youtube_formats_deduplicate(url):
    assert link_source(url)["id"] == "youtube:aircAruvnKk"
    assert youtube_id("https://youtube.com.evil.test/watch?v=aircAruvnKk") is None


def test_caption_groups_preserve_start_time(configured):
    snippets = [SimpleNamespace(text="A qubit is the unit of quantum information.", start=12.8),
                SimpleNamespace(text="A qubit can exist in a superposition of basis states.", start=110)]
    docs, warnings = load_sources.transcript_documents(snippets, {"source_url": "https://www.youtube.com/watch?v=aircAruvnKk"})
    assert [d.metadata["timestamp"] for d in docs] == [12, 110]
    assert docs[1].metadata["source_url"].endswith("&t=110s")
    assert not warnings


def test_supplied_transcript_works_without_youtube_request(configured, monkeypatch):
    monkeypatch.setattr(load_sources, "load_youtube", lambda *_: pytest.fail("Supplied transcript must work offline"))
    text = "WEBVTT\n\n00:00:00.000 --> 00:00:03.000\nA qubit is the basic unit of quantum information."
    item = {**link_source("https://youtu.be/aircAruvnKk"), "transcript": text}
    docs, warnings = load_sources.load_source(item, configured / "captions", {})
    assert docs[0].page_content == "A qubit is the basic unit of quantum information."
    assert warnings and "timestamp" not in docs[0].metadata


def test_unavailable_youtube_has_actionable_fallback(configured, monkeypatch):
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import TranscriptsDisabled
    def unavailable(*args):
        raise TranscriptsDisabled("aircAruvnKk")
    monkeypatch.setattr(YouTubeTranscriptApi, "list", unavailable)
    with pytest.raises(ServiceError, match="pasted or uploaded transcript"):
        load_sources.load_youtube(link_source("https://youtu.be/aircAruvnKk"), configured, {})


@pytest.mark.parametrize("addresses", [["127.0.0.1"], ["10.0.0.1"], ["169.254.169.254"], ["::1"], ["93.184.216.34", "192.168.1.1"]])
def test_web_rejects_private_or_mixed_dns(monkeypatch, addresses):
    monkeypatch.setattr(public_web.socket, "getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", (ip, 443)) for ip in addresses])
    with pytest.raises(ServiceError, match="Private, local"):
        public_web.public_target("https://example.com/lesson")


def test_redirect_revalidates_destination(configured, monkeypatch):
    def resolve(host, *_a, **_k):
        return [(2, 1, 6, "", ("127.0.0.1" if host == "localhost" else "93.184.216.34", 80))]
    monkeypatch.setattr(public_web.socket, "getaddrinfo", resolve)
    response = SimpleNamespace(status=302, headers={"Location": "http://localhost/private"}, close=lambda: None)
    calls = []
    def pool(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(urlopen=lambda *_a, **_k: response, close=lambda: None)
    monkeypatch.setattr(public_web.urllib3, "HTTPConnectionPool", pool)
    with pytest.raises(ServiceError, match="Private, local"):
        public_web.download_public("http://example.com/lesson", configured)
    assert len(calls) == 1 and calls[0]["host"] == "93.184.216.34"


def test_drive_stream_accepts_over_30mb(configured, monkeypatch):
    info = {"name": "large.pdf", "mimeType": "application/pdf", "size": str(31 * 1024 * 1024), "capabilities": {"canDownload": True}}
    monkeypatch.setattr(download_files, "file_info", lambda *_: info)
    class Downloader:
        def __init__(self, output, request, chunksize):
            self.output, self.remaining = output, 31
            assert chunksize == 1024 * 1024
        def next_chunk(self, **kwargs):
            self.output.write(b"x" * 1024 * 1024)
            self.remaining -= 1
            return None, self.remaining == 0
    monkeypatch.setattr(download_files, "MediaIoBaseDownload", Downloader)
    drive = SimpleNamespace(files=lambda: SimpleNamespace(get_media=lambda **_: object()))
    path, _ = download_files.download_file(drive, "large", configured / "raw")
    assert path.stat().st_size == 31 * 1024 * 1024
    assert not list(path.parent.glob("*.part"))


def test_large_recording_reports_need_for_transcript(configured):
    with pytest.raises(ServiceError, match="audio/video recording"):
        download_files.validate_download({"mimeType": "video/mp4", "size": str(610 * 1024 * 1024), "capabilities": {"canDownload": True}})


def test_html_removes_navigation_and_scripts(configured):
    path = configured / "lesson.html"
    path.write_text('<html><nav>Unrelated menu</nav><script>secret script</script><article><h1>Quantum information</h1><p>A qubit is the basic unit of quantum information and can exist in a superposition of two basis states.</p></article></html>', encoding="utf-8")
    docs, _ = load_documents(path, {})
    assert "qubit" in docs[0].page_content
    assert "Unrelated" not in docs[0].page_content and "secret" not in docs[0].page_content


def test_xlsx_retains_sheet_location(configured):
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.active.title = "Quantum notes"
    workbook.active.append(["Qubit", "A qubit is the basic unit of quantum information."])
    path = configured / "lesson.xlsx"
    workbook.save(path)
    docs, _ = load_documents(path, {})
    assert docs[0].metadata["section"] == "Quantum notes"
    assert "basic unit" in docs[0].page_content


def test_epub_extracts_readable_lesson(configured):
    from zipfile import ZipFile
    path = configured / "lesson.epub"
    with ZipFile(path, "w") as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("META-INF/container.xml", '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        book.writestr("content.opf", '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">test-lesson</dc:identifier><dc:title>Quantum lesson</dc:title><dc:language>en</dc:language></metadata><manifest><item id="lesson" href="lesson.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="lesson"/></spine></package>')
        book.writestr("lesson.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Qubits</title></head><body><p>A qubit is the basic unit of quantum information.</p></body></html>')
    docs, _ = load_documents(path, {})
    assert "basic unit of quantum information" in " ".join(docs[0].page_content.split())


def test_added_sources_are_private_and_validated(seeded, monkeypatch):
    from backend import main, storage
    monkeypatch.setattr(main, "enqueue_import", lambda *_: "job")
    token = storage.create_session("alice")
    headers = {"Authorization": "Bearer " + token}
    body = {"url": "https://youtu.be/aircAruvnKk", "transcript": "A qubit is the basic unit of quantum information."}
    with TestClient(main.app) as client:
        assert client.post("/courses/math/sources", json=body).status_code == 401
        assert client.post("/courses/foreign/sources", headers=headers, json=body).status_code == 404
        assert client.post("/courses/math/sources", headers=headers, json={"url": "file:///private"}).status_code == 422
        assert client.post("/courses/math/sources", headers=headers, json=body).status_code == 202
    assert len(storage.extra_sources("alice", "math")) == 1
    assert storage.extra_sources("bob", "math") == []
    assert storage.extra_sources("alice", "physics") == []
