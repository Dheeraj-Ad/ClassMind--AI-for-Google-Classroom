"""YouTube captions, user-supplied transcripts, webpages and Classroom text."""
import html
import json
import re

from langchain_core.documents import Document
import requests

from backend.classroom.public_web import download_public
from backend.config import settings
from backend.storage import digest
from backend.utils.errors import ServiceError
from backend.utils.file_processor import clean_text, load_documents


class BoundedSession(requests.Session):
    def request(self, *args, **kwargs):
        kwargs.setdefault("timeout", (10, 25))
        return super().request(*args, **kwargs)


def transcript_documents(snippets, metadata):
    documents, parts, start, length = [], [], None, 0
    limit = settings().max_extracted_chars
    truncated = False
    def flush():
        if not parts:
            return
        timestamp = int(start or 0)
        documents.append(Document(page_content=" ".join(parts), metadata={**metadata, "page": 0, "timestamp": timestamp,
            "source_url": metadata["source_url"] + f"&t={timestamp}s"}))
    for snippet in snippets:
        text = clean_text(html.unescape(snippet.text))
        if not text or re.fullmatch(r"\[(music|applause|silence)\]", text, re.I):
            continue
        if length + len(text) > limit:
            truncated = True
            break
        if parts and (sum(map(len, parts)) > 1100 or snippet.start - start > 90):
            flush()
            parts, start = [], None
        if start is None:
            start = snippet.start
        parts.append(text)
        length += len(text)
    flush()
    if not documents:
        raise ServiceError("The video captions contain no usable speech text.")
    return documents, (["Transcript was truncated at the configured extraction limit."] if truncated else [])


def load_youtube(item, directory, metadata):
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (AgeRestricted, IpBlocked, NoTranscriptFound, RequestBlocked,
                                                TranscriptsDisabled, VideoUnavailable, VideoUnplayable)
    warnings = []
    try:
        with BoundedSession() as session:
            api = YouTubeTranscriptApi(http_client=session)
            available = api.list(item["video_id"])
            languages = [s.strip() for s in settings().youtube_languages.split(",") if s.strip()]
            try:
                transcript = available.find_transcript(languages)
            except NoTranscriptFound:
                transcript = next(iter(available), None)
                if transcript is None:
                    raise ServiceError("No captions are available. Add this link with a pasted or uploaded transcript in the Library.")
                warnings.append(f"Imported captions in {transcript.language}; no preferred-language captions were available.")
            fetched = transcript.fetch()
            if transcript.is_generated:
                warnings.append("These are auto-generated captions; transcription errors may be present.")
            documents, notes = transcript_documents(fetched, metadata)
    except (TranscriptsDisabled, NoTranscriptFound):
        raise ServiceError("YouTube captions are unavailable. Add this link with a pasted or uploaded transcript in the Library.")
    except (IpBlocked, RequestBlocked):
        raise ServiceError("YouTube blocked the transcript request. Add a transcript in the Library or try re-syncing later; video restrictions are not bypassed.")
    except (VideoUnavailable, VideoUnplayable, AgeRestricted):
        raise ServiceError("The YouTube video is private, removed, age-restricted, or otherwise unavailable.")
    except ServiceError:
        raise
    except Exception as exc:
        raise ServiceError("The YouTube transcript could not be retrieved. Add a transcript in the Library or re-sync later.") from exc
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (digest(item["id"])[:32] + ".json")).write_text(
        json.dumps([{"text": d.page_content, "timestamp": d.metadata["timestamp"]} for d in documents], ensure_ascii=False), encoding="utf-8")
    return documents, warnings + notes


def load_source(item, directory, metadata):
    kind = item.get("source_type", "drive")
    if item.get("transcript") or kind == "post":
        text = item.get("transcript") or item.get("text", "")
        text = re.sub(r"(?m)^\s*(?:WEBVTT|\d+|\d{1,2}:\d{2}[^\n]*-->[^\n]*)\s*$", "", text)
        text = clean_text(text)
        if len(text) < 30:
            raise ServiceError("This source contains too little text to index.")
        limit = settings().max_extracted_chars
        warnings = ["Only the beginning of this source was indexed because it exceeds the extraction limit."] if len(text) > limit else []
        if item.get("transcript"):
            warnings.append("User-supplied transcript; timestamp alignment is not available.")
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (digest(item["id"])[:32] + ".txt")).write_text(text[:limit], encoding="utf-8")
        return [Document(page_content=text[:limit], metadata={**metadata, "page": 0})], warnings
    if kind == "youtube":
        return load_youtube(item, directory, metadata)
    if kind == "web":
        path, final_url = download_public(item["source_url"], directory)
        return load_documents(path, {**metadata, "source_url": final_url})
    raise ServiceError("No source reader is available for this attachment.")
