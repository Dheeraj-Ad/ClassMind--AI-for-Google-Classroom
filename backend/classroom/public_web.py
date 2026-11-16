"""Bounded public URL downloads with pinned DNS and redirect validation."""
import ipaddress
from pathlib import Path
import socket
import time
from urllib.parse import quote, urljoin, urlsplit

import certifi
import urllib3

from backend.config import settings
from backend.storage import digest
from backend.utils.errors import ServiceError
from backend.classroom.download_files import MIME_EXTENSIONS


def public_target(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ServiceError("Only public HTTP/HTTPS links without embedded credentials can be imported.")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ServiceError("The link contains an invalid port.") from exc
    if port not in {80, 443}:
        raise ServiceError("Web imports support standard HTTP/HTTPS ports only.")
    hostname = parsed.hostname.encode("idna").decode()
    try:
        addresses = list(dict.fromkeys(item[4][0] for item in socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)))
    except OSError as exc:
        raise ServiceError("The website could not be found. Check the URL or your internet connection.") from exc
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ServiceError("Private, local, or reserved network addresses cannot be imported.")
    addresses.sort(key=lambda address: ":" in address)
    return parsed, hostname, port, addresses[0]


def download_public(url, directory: Path):
    original = url
    started = time.monotonic()
    for _ in range(5):
        parsed, hostname, port, address = public_target(url)
        options = {"host": address, "port": port, "timeout": urllib3.Timeout(connect=10, read=30)}
        if parsed.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(**options, server_hostname=hostname, assert_hostname=hostname,
                                               cert_reqs="CERT_REQUIRED", ca_certs=certifi.where())
        else:
            pool = urllib3.HTTPConnectionPool(**options)
        response, temporary = None, None
        try:
            target = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
            if parsed.query:
                target += "?" + quote(parsed.query, safe="%=&?/:;+,-._~")
            response = pool.urlopen("GET", target, redirect=False, preload_content=False, retries=False,
                                    headers={"Host": hostname, "User-Agent": "ClassMind/1.2 study-material-reader",
                                             "Accept-Encoding": "identity"})
            if response.status in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                if not location:
                    raise ServiceError("The webpage returned an incomplete redirect.")
                url = urljoin(url, location)
                continue
            if response.status in {401, 403}:
                raise ServiceError("This website requires sign-in or blocks automated access. Attach an exported document or paste its text instead.")
            if response.status == 429:
                raise ServiceError("The website is rate-limiting requests. Re-sync this source later.")
            if response.status != 200:
                raise ServiceError(f"The website returned HTTP {response.status}; this source was not imported.")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            extension = MIME_EXTENSIONS.get(mime)
            if not extension:
                suffix = Path(parsed.path).suffix.lower()
                if mime in {"", "application/octet-stream"} and suffix in {".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".epub"}:
                    extension = suffix
                else:
                    raise ServiceError("This URL does not serve a supported document or readable HTML page.")
            cap = (min(settings().max_download_mb, 8) if extension == ".html" else settings().max_download_mb) * 1024 * 1024
            try:
                if int(response.headers.get("Content-Length", 0)) > cap:
                    raise ServiceError(f"This web source exceeds its {cap // (1024 * 1024)} MB limit.")
            except ValueError:
                pass
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (digest(original)[:32] + extension)
            temporary = path.with_suffix(extension + ".part")
            total = 0
            with temporary.open("wb") as output:
                for block in response.stream(64 * 1024, decode_content=True):
                    total += len(block)
                    if total > cap or time.monotonic() - started > 120:
                        raise ServiceError("The web download exceeded its size or time limit.")
                    output.write(block)
            temporary.replace(path)
            return path, url
        except ServiceError:
            raise
        except (urllib3.exceptions.HTTPError, OSError) as exc:
            raise ServiceError("The public link could not be downloaded securely. Check connectivity or provide an exported document.") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            if response is not None:
                response.close()
            pool.close()
    raise ServiceError("The public link redirected too many times.")
