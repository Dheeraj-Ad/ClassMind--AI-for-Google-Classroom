"""Bounded Gemini generation; keys, Google IDs and full files stay out of prompts."""
import json
import re
import threading

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.config import settings
from prompts.system_prompts import GEMINI_SYSTEM_PROMPT


class GeminiUnavailable(Exception):
    """Sanitized, user-facing provider failure; never contains an API response."""


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str = Field(pattern=r"^S[1-4]$")
    quote: str = Field(min_length=15, max_length=1000)


class Segment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=2200)
    citations: list[Citation] = Field(max_length=4)


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    supported: bool
    answer: Segment
    details: list[Segment] = Field(max_length=3)


_thread = threading.local()


def session():
    if not hasattr(_thread, "http"):
        _thread.http = requests.Session()
    return _thread.http


def normalize(value):
    return re.sub(r"\s+", " ", value).strip()


def generate_answer(question, query, documents, mode):
    config = settings()
    key = config.gemini_api_key.get_secret_value()
    if not key:
        raise GeminiUnavailable("Gemini is not configured. Showing local evidence instead.")
    sources = [{"source_id": f"S{i}", "text": normalize(doc.page_content)[:3500]}
               for i, doc in enumerate(documents[:4], 1)]
    payload = {
        "systemInstruction": {"parts": [{"text": GEMINI_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps({
            "question": question[:2000], "search_context": query[:2000], "mode": mode, "sources": sources}, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": config.gemini_max_output_tokens,
            "responseMimeType": "application/json", "responseJsonSchema": GroundedAnswer.model_json_schema(),
            "thinkingConfig": {"thinkingLevel": "minimal"}},
    }
    try:
        response = session().post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{config.gemini_model}:generateContent",
            headers={"x-goog-api-key": key}, json=payload, timeout=(4, config.gemini_timeout_seconds), allow_redirects=False)
    except requests.Timeout:
        raise GeminiUnavailable("Gemini took too long. Showing local evidence instead.") from None
    except requests.RequestException:
        raise GeminiUnavailable("Could not reach Gemini. Showing local evidence instead.") from None
    if response.status_code in {401, 403}:
        raise GeminiUnavailable("Gemini authentication or access failed. Check the configured key; showing local evidence instead.")
    if response.status_code == 429:
        raise GeminiUnavailable("Gemini's quota or rate limit was reached. Showing local evidence instead.")
    if not response.ok:
        raise GeminiUnavailable("Gemini could not complete the request. Showing local evidence instead.")
    try:
        candidates = response.json().get("candidates", [])
        candidate = candidates[0] if candidates else {}
        if candidate.get("finishReason") != "STOP":
            raise ValueError("Incomplete or blocked response")
        text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []) if not p.get("thought"))
        result = GroundedAnswer.model_validate_json(text)
        if result.supported:
            context = {s["source_id"]: s["text"] for s in sources}
            for segment in [result.answer, *result.details]:
                if not segment.text.strip() or not segment.citations:
                    raise ValueError("Missing citation")
                for citation in segment.citations:
                    if citation.source_id not in context or normalize(citation.quote) not in context[citation.source_id]:
                        raise ValueError("Citation does not match supplied source")
        return result
    except (ValueError, TypeError, AttributeError, ValidationError):
        raise GeminiUnavailable("Gemini returned an incomplete answer or references that could not be checked. Showing local evidence instead.") from None
