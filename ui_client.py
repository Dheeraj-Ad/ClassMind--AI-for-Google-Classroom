"""Per-browser-session API client. Never share authenticated caches across users."""
from copy import deepcopy
import json
import time
from urllib.parse import urlsplit, urlunsplit

import requests


class APIError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def backend_address(url):
    # The local server binds IPv4. Windows' IPv6 localhost fallback costs ~2 s
    # per request. Browser OAuth URLs stay localhost so existing cookies work.
    parts = urlsplit(url.rstrip("/"))
    if parts.scheme == "http" and parts.hostname == "localhost" and not parts.username:
        parts = parts._replace(netloc="127.0.0.1" + (f":{parts.port}" if parts.port else ""))
    return urlunsplit(parts)


class BackendClient:
    def __init__(self, base_url, token):
        self.base_url = backend_address(base_url)
        self.token = token
        self.session = requests.Session()
        if urlsplit(self.base_url).hostname in {"127.0.0.1", "::1", "localhost"}:
            self.session.trust_env = False
        self.cache = {}

    def close(self):
        self.cache.clear()
        self.session.close()

    def invalidate(self):
        self.cache.clear()

    def request(self, method, path, *, ttl=0, refresh=False, **kwargs):
        key = (path, json.dumps(kwargs.get("params", {}), sort_keys=True))
        cached = self.cache.get(key)
        if method == "GET" and not refresh and cached and cached[0] > time.monotonic():
            return deepcopy(cached[1])
        if method != "GET":
            self.invalidate()
        read_timeout = 90 if path.endswith("/chat") else 45 if path.endswith("/refresh") else 10
        try:
            response = self.session.request(method, self.base_url + path,
                headers={"Authorization": "Bearer " + self.token}, timeout=(2, read_timeout), **kwargs)
        except requests.Timeout as exc:
            message = "The request took too long."
            if method != "GET":
                message += " It may still be processing; refresh the page before submitting it again."
            else:
                message += " Check the backend, then retry."
            raise APIError(message) from exc
        except requests.RequestException as exc:
            raise APIError("Cannot reach ClassMind. Start the backend, then retry.") from exc
        if not response.ok:
            try:
                message = response.json().get("detail", "The request could not be completed.")
            except ValueError:
                message = "The backend returned an unexpected response."
            if response.status_code == 401:
                self.invalidate()
            raise APIError(str(message), response.status_code)
        try:
            data = response.json()
        except ValueError as exc:
            raise APIError("The backend returned an unreadable response. Please retry.") from exc
        if method == "GET" and ttl:
            if len(self.cache) >= 32:
                self.cache.pop(next(iter(self.cache)))
            self.cache[key] = (time.monotonic() + ttl, deepcopy(data))
        return data
