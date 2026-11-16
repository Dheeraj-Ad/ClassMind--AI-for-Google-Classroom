"""Browser-bound OAuth state, PKCE, validated identity, encrypted refresh tokens."""
import json
import logging
import secrets
import threading
import time
from urllib.parse import urlparse

from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request
from google.oauth2 import id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from oauthlib.oauth2 import OAuth2Error
from requests.exceptions import RequestException, SSLError, Timeout

from backend.config import settings
from backend.storage import cipher, db, digest, get_user, save_user
from backend.utils.errors import ServiceError

REQUIRED_SCOPES = [
    "openid", "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
ASSIGNMENT_SCOPE = "https://www.googleapis.com/auth/classroom.coursework.me.readonly"
SCOPES = [*REQUIRED_SCOPES, ASSIGNMENT_SCOPE]
_refresh_locks = [threading.Lock() for _ in range(64)]
log = logging.getLogger(__name__)
SCOPE_ALIASES = {
    "email": "https://www.googleapis.com/auth/userinfo.email",
    "profile": "https://www.googleapis.com/auth/userinfo.profile",
}


def normalized_scopes(scopes):
    if isinstance(scopes, str):
        scopes = scopes.split()
    return {SCOPE_ALIASES.get(scope, scope) for scope in (scopes or [])}


def validate_granted_scopes(scopes):
    missing = set(REQUIRED_SCOPES) - normalized_scopes(scopes)
    if missing:
        labels = sorted(scope.rsplit("/", 1)[-1] for scope in missing)
        raise ServiceError("Google permissions are missing: " + ", ".join(labels) +
                           ". Return to ClassMind, connect again, and select all requested permissions.", 403)


def configure_scope_response(flow):
    """Accept actual Google grants only after checking every required permission.

    OAuthlib normally rejects *any* scope difference before we can check granular
    consent. Keep its validation enabled; acknowledge the response's scope list
    on this session only, after validating aliases and the required subset.
    """
    def validate_response(response):
        try:
            token = response.json()
        except ValueError:
            return response  # Let the OAuth client report a malformed response.
        if response.ok and isinstance(token, dict) and "access_token" in token and "scope" in token:
            validate_granted_scopes(token["scope"])
            granted = token["scope"]
            flow.oauth2session.scope = granted.split() if isinstance(granted, str) else granted
        return response
    flow.oauth2session.register_compliance_hook("access_token_response", validate_response)


def exchange_token(flow, code):
    configure_scope_response(flow)
    try:
        flow.fetch_token(code=code, timeout=30)
    except ServiceError:
        raise
    except OAuth2Error as exc:
        # Do not log exception text: provider responses may contain credentials/codes.
        messages = {
            "invalid_grant": "Google rejected an expired, reused, or invalid login code. Return to ClassMind and click Connect Google Classroom again; do not reload the callback page.",
            "invalid_client": "Google rejected the OAuth client credentials. Replace backend/auth/credentials.json with a fresh Web application JSON downloaded from the same Google Cloud project, then restart the backend.",
            "redirect_uri_mismatch": "Google rejected the redirect URI. Register the exact GOOGLE_REDIRECT_URI from .env on this OAuth Web application client.",
            "access_denied": "Google denied this login. Check the OAuth test-user list, requested permissions, and school administrator policy.",
        }
        code_name = exc.error if exc.error in messages else "token_exchange_failed"
        log.warning("Google OAuth token exchange failed (%s)", code_name)
        raise ServiceError(messages.get(code_name, "Google could not exchange the login code. Start a fresh login and check your OAuth client configuration.") + f" [oauth:{code_name}]", 401) from exc
    except SSLError as exc:
        raise ServiceError("The backend could not verify Google's HTTPS certificate. Check your proxy, antivirus HTTPS inspection, and trusted certificates. [oauth:tls_failed]", 503) from exc
    except (Timeout, RequestException) as exc:
        raise ServiceError("The backend could not reach Google's token service. Check your internet connection or proxy, then start a fresh login. [oauth:network_failed]", 503) from exc
    except Warning as exc:
        raise ServiceError("Google returned a different permission set. Start a fresh login and grant all requested permissions. [oauth:scope_changed]", 403) from exc


def client_config():
    path = settings().path(settings().google_credentials_file)
    try:
        config = json.loads(path.read_text(encoding="utf-8-sig"))
        if "REPLACE" in config["web"]["client_id"]:
            raise ValueError("Placeholder credentials")
        return config
    except (OSError, ValueError, KeyError) as exc:
        raise ServiceError("Install your Google Web application credentials in backend/auth/credentials.json.", 503) from exc


def begin_login():
    state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    flow = Flow.from_client_config(client_config(), scopes=SCOPES, state=state, code_verifier=verifier)
    flow.redirect_uri = settings().google_redirect_uri
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", nonce=state)
    with db() as con:
        con.execute("DELETE FROM oauth WHERE expires < ?", (time.time(),))
        con.execute("INSERT INTO oauth VALUES (?,?,?,?)",
                    (state, digest(binding), cipher().encrypt(verifier.encode()), time.time() + 600))
    return url, binding


def finish_login(state: str, binding: str, code: str):
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute("SELECT * FROM oauth WHERE state=?", (state,)).fetchone()
        if not row or row["expires"] < time.time() or not secrets.compare_digest(row["binding"], digest(binding)):
            raise ServiceError("Login expired or browser verification failed. Start again.", 400)
        con.execute("DELETE FROM oauth WHERE state=?", (state,))
    config = client_config()
    flow = Flow.from_client_config(config, scopes=SCOPES, state=state,
                                  code_verifier=cipher().decrypt(row["verifier"]).decode())
    flow.redirect_uri = settings().google_redirect_uri
    # Passing code directly avoids enabling insecure OAuth transport globally for localhost.
    exchange_token(flow, code)
    credentials = flow.credentials
    validate_granted_scopes(credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes)
    try:
        claims = id_token.verify_oauth2_token(credentials.id_token, Request(), config["web"]["client_id"], clock_skew_in_seconds=30)
    except (TransportError, RequestException) as exc:
        raise ServiceError("The backend could not download Google's identity-verification certificates. Check your internet connection and retry. [oauth:certificate_fetch_failed]", 503) from exc
    except ValueError as exc:
        raise ServiceError("Google's identity token could not be verified. Sync your computer's date/time, confirm the OAuth client configuration, and start a fresh login. [oauth:identity_validation_failed]", 401) from exc
    if claims.get("nonce") != state or not claims.get("sub"):
        raise ServiceError("Google identity could not be verified.", 401)
    user_id = claims["sub"]
    if not credentials.refresh_token:
        try:
            _, previous = get_user(user_id)
            data = json.loads(credentials.to_json())
            data["refresh_token"] = json.loads(previous).get("refresh_token")
            credentials = Credentials.from_authorized_user_info(data)
        except ValueError:
            raise ServiceError("No refresh token received. Remove ClassMind from Google account connections and reconnect.", 401)
    save_user(user_id, {"name": claims.get("name", "Student"), "email": claims.get("email", "")}, credentials.to_json())
    return user_id


def get_credentials(user_id: str):
    with _refresh_locks[hash(user_id) % len(_refresh_locks)]:
        profile, serialized = get_user(user_id)
        # Preserve the actual granted scopes; never re-add a declined permission.
        credentials = Credentials.from_authorized_user_info(json.loads(serialized))
        if not credentials.valid:
            if not credentials.refresh_token:
                raise ServiceError("Reconnect Google Classroom to renew access.", 401)
            try:
                credentials.refresh(Request())
            except RefreshError as exc:
                raise ServiceError("Google access was revoked or expired. Reconnect Classroom.", 401) from exc
            save_user(user_id, profile, credentials.to_json())
        return credentials


def validate_origins():
    config = settings()
    urls = [urlparse(config.public_backend_url), urlparse(config.streamlit_url), urlparse(config.google_redirect_uri)]
    if len({url.hostname for url in urls}) != 1:
        raise RuntimeError("PUBLIC_BACKEND_URL, STREAMLIT_URL, and GOOGLE_REDIRECT_URI must share a hostname for secure session cookies.")
    if not config.cookie_secure and any(url.hostname not in {"localhost", "127.0.0.1"} for url in urls):
        raise RuntimeError("Non-local deployments require COOKIE_SECURE=true and HTTPS.")
    if config.cookie_secure and any(url.scheme != "https" for url in urls):
        raise RuntimeError("Secure cookies require HTTPS on all public URLs.")
