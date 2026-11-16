"""Google clients are created per operation: httplib2 clients are not thread-safe."""
import httplib2
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from backend.auth.google_auth import get_credentials
from backend.storage import db
from backend.utils.errors import google_error
import json


def service(user_id, name, version):
    http = AuthorizedHttp(get_credentials(user_id), http=httplib2.Http(timeout=30))
    return build(name, version, http=http, cache_discovery=False)


def execute(request):
    try:
        return request.execute(num_retries=3)
    except HttpError as exc:
        raise google_error(exc) from exc


def fetch_courses(user_id):
    client = service(user_id, "classroom", "v1")
    courses, page = [], None
    while True:
        result = execute(client.courses().list(studentId="me", courseStates=["ACTIVE"], pageSize=100, pageToken=page))
        courses.extend(result.get("courses", []))
        page = result.get("nextPageToken")
        if not page:
            break
    # Replace the authorization cache only after all pages succeed.
    with db() as con:
        con.execute("DELETE FROM courses WHERE user_id=?", (user_id,))
        con.executemany("INSERT INTO courses VALUES (?,?,?)",
                        [(user_id, c["id"], json.dumps(c)) for c in courses])
    return courses


def require_course(user_id, course_id):
    from backend.utils.errors import ServiceError
    with db() as con:
        row = con.execute("SELECT payload FROM courses WHERE user_id=? AND id=?", (user_id, course_id)).fetchone()
    if not row:
        raise ServiceError("Course is not in your connected active courses. Refresh the dashboard.", 404)
    return json.loads(row[0])
