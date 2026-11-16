"""Normalize Classroom attachment URLs without fetching them."""
import re
from urllib.parse import parse_qs, urlparse

from backend.storage import digest


def youtube_id(url):
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    candidate = ""
    if host in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}:
        if parsed.path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
        elif parsed.path.startswith(("/embed/", "/shorts/", "/live/")):
            candidate = parsed.path.split("/")[2]
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else None


def link_source(url, title="", origin="link"):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use a public HTTP or HTTPS URL without embedded credentials.")
    video = youtube_id(url)
    if video:
        return {"id": "youtube:" + video, "video_id": video, "file_name": title or "YouTube lesson",
                "source_type": "youtube", "source_url": "https://www.youtube.com/watch?v=" + video, "origin": origin}
    if parsed.hostname in {"drive.google.com", "docs.google.com"}:
        match = re.search(r"/d/([A-Za-z0-9_-]+)", parsed.path)
        drive_id = match.group(1) if match else parse_qs(parsed.query).get("id", [""])[0]
        if drive_id:
            return {"id": drive_id, "file_name": title or "Linked Google document", "source_type": "drive",
                    "source_url": url, "origin": origin}
    return {"id": "web:" + digest(url)[:32], "file_name": title or parsed.hostname,
            "source_type": "web", "source_url": url, "origin": origin}
