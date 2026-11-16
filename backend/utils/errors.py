"""Public errors deliberately exclude tokens, raw provider responses, and local paths."""
import json


class ServiceError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def google_error(error):
    code = getattr(getattr(error, "resp", None), "status", 500)
    try:
        reasons = [e.get("reason") for e in json.loads(error.content)["error"].get("errors", [])]
    except (ValueError, KeyError, TypeError, AttributeError):
        reasons = []
    if code == 429 or any(r in {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"} for r in reasons):
        return ServiceError("Google rate limit reached after retries. Please re-sync later.", 429)
    if "exportSizeLimitExceeded" in reasons:
        return ServiceError("Google limits native document exports to 10 MB. Attach an exported PDF or Office file to import a larger document.")
    if code == 401:
        return ServiceError("Google authorization expired or was revoked. Reconnect Classroom.", 401)
    if code == 403:
        return ServiceError("Google denied access. Check file sharing, granted scopes, and school administrator policy.", 403)
    if code == 404:
        return ServiceError("The Google file or course was removed or is not accessible.", 404)
    return ServiceError("Google is temporarily unavailable. Please try again.", 503)
