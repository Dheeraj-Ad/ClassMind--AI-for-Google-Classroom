"""Bounded Drive downloads. File IDs and titles never become filesystem paths."""
from pathlib import Path

from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from backend.classroom.fetch_courses import execute
from backend.config import settings
from backend.storage import digest
from backend.utils.errors import ServiceError, google_error

MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/epub+zip": ".epub", "application/json": ".txt",
    "text/html": ".html", "application/xhtml+xml": ".html",
    "text/vtt": ".txt", "application/x-subrip": ".txt",
    "text/plain": ".txt", "text/markdown": ".txt", "text/csv": ".txt",
}
GOOGLE_EXPORTS = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}


def file_info(drive, file_id):
    return execute(drive.files().get(fileId=file_id, supportsAllDrives=True,
                                    fields="id,name,mimeType,size,modifiedTime,capabilities(canDownload),webViewLink"))


def validate_download(info):
    if not info.get("capabilities", {}).get("canDownload", False):
        raise ServiceError("Download is disabled by the owner or your school.")
    mime = info["mimeType"]
    if mime.startswith(("audio/", "video/")):
        raise ServiceError("This is an audio/video recording. Add its Drive link with a transcript in Source Library; speech-to-text is not enabled. YouTube links with available captions can be imported directly.")
    if mime not in GOOGLE_EXPORTS and mime not in MIME_EXTENSIONS:
        raise ServiceError(f"Unsupported file type: {mime}. Import documents, spreadsheets, EPUB, HTML, or text.")
    limit = settings().max_download_mb * 1024 * 1024
    if int(info.get("size", 0)) > limit:
        raise ServiceError(f"File exceeds the {settings().max_download_mb} MB download limit.")


def download_file(drive, file_id: str, directory: Path):
    info = file_info(drive, file_id)
    validate_download(info)
    mime = info["mimeType"]
    limit = settings().max_download_mb * 1024 * 1024
    if mime in GOOGLE_EXPORTS:
        export_mime, extension = GOOGLE_EXPORTS[mime]
        request = drive.files().export_media(fileId=file_id, mimeType=export_mime)
    elif mime in MIME_EXTENSIONS:
        extension = MIME_EXTENSIONS[mime]
        request = drive.files().get_media(fileId=file_id, supportsAllDrives=True)
    else:
        raise ServiceError(f"Unsupported file type: {mime}. Supported: PDF, DOCX, PPTX, text and exported Google documents.")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (digest(file_id)[:32] + extension)
    temporary = destination.with_suffix(extension + ".part")
    try:
        with temporary.open("wb") as output:
            downloader = MediaIoBaseDownload(output, request, chunksize=1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=3)
                if output.tell() > limit:
                    raise ServiceError("Downloaded file exceeds the configured size limit.")
        temporary.replace(destination)
    except HttpError as exc:
        temporary.unlink(missing_ok=True)
        raise google_error(exc) from exc
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination, info
