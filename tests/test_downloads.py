from types import SimpleNamespace

import pytest

from backend.classroom import download_files
from backend.utils.errors import ServiceError


def drive_file(info):
    calls = {}
    class Files:
        def get(self, **kwargs):
            return SimpleNamespace(execute=lambda **_: info)
        def export_media(self, **kwargs):
            calls["export"] = kwargs
            return object()
        def get_media(self, **kwargs):
            calls["download"] = kwargs
            return object()
    return SimpleNamespace(files=Files), calls


def test_google_doc_is_exported_and_untrusted_name_cannot_escape(tmp_path, monkeypatch):
    drive, calls = drive_file({"name": "../../escape", "mimeType": "application/vnd.google-apps.document", "capabilities": {"canDownload": True}})
    class Downloader:
        def __init__(self, output, request, **kwargs):
            self.output = output
        def next_chunk(self, **kwargs):
            self.output.write(b"sample-pdf")
            return None, True
    monkeypatch.setattr(download_files, "MediaIoBaseDownload", Downloader)
    path, _ = download_files.download_file(drive, "../../id", tmp_path)
    assert path.parent == tmp_path
    assert path.suffix == ".pdf"
    assert calls["export"]["mimeType"] == "application/pdf"


@pytest.mark.parametrize("info,reason", [
    ({"mimeType": "application/pdf", "capabilities": {"canDownload": False}}, "disabled"),
    ({"mimeType": "video/mp4", "capabilities": {"canDownload": True}}, "audio/video recording"),
    ({"mimeType": "application/pdf", "size": str(1000 * 1024 * 1024), "capabilities": {"canDownload": True}}, "exceeds"),
])
def test_files_are_rejected_before_download(tmp_path, info, reason):
    drive, calls = drive_file(info)
    with pytest.raises(ServiceError, match=reason):
        download_files.download_file(drive, "file", tmp_path)
    assert calls == {}
