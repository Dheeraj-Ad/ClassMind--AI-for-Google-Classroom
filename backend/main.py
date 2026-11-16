"""FastAPI owns all secrets, Google access, jobs, persistence and retrieval."""
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from backend.auth.google_auth import begin_login, finish_login, validate_origins
from backend.classroom.fetch_courses import fetch_courses, require_course
from backend.config import settings
from backend.rag.chain import answer_question
from backend.rag.ingest import delete_knowledge_base, ingest_course
from backend.storage import append_exchange, create_session, db, digest, get_user, history, initialize, manifest, recent_history, save_extra_source, session_user
from backend.utils.errors import ServiceError
from backend.utils.locks import course_lock

log = logging.getLogger(__name__)
executor = None


@asynccontextmanager
async def lifespan(app):
    global executor
    validate_origins()
    initialize()
    executor = ThreadPoolExecutor(max_workers=settings().import_workers, thread_name_prefix="classmind-import")
    yield
    executor.shutdown(wait=True, cancel_futures=True)


app = FastAPI(title="ClassMind", version="1.2.0", lifespan=lifespan)


@app.exception_handler(ServiceError)
async def service_error_handler(request, error):
    return JSONResponse({"detail": error.message}, status_code=error.status_code)


@app.exception_handler(Exception)
async def unhandled_error_handler(request, error):
    log.error("Request failed (%s)", type(error).__name__)
    return JSONResponse({"detail": "An internal operation failed. Please retry or check backend configuration."}, status_code=500)


def current_user(authorization: str = Header(default="")):
    # Mutations use a bearer header supplied by Streamlit, never ambient browser cookies.
    if not authorization.startswith("Bearer "):
        raise ServiceError("Connect Google Classroom to continue.", 401)
    user = session_user(authorization[7:])
    if not user:
        raise ServiceError("Session expired. Reconnect Google Classroom.", 401)
    return user


def enqueue_import(user_id, course_id):
    job_id = uuid.uuid4().hex
    with db() as con:
        active = con.execute("SELECT id FROM jobs WHERE user_id=? AND course_id=? AND status IN ('queued','running')",
                             (user_id, course_id)).fetchone()
    if active:
        return active[0]
    lock = course_lock(user_id, course_id)
    if not lock.acquire(blocking=False):
        raise ServiceError("This course is busy. Try again shortly.", 409)
    try:
        with db() as con:
            con.execute("BEGIN IMMEDIATE")
            active = con.execute("SELECT id FROM jobs WHERE user_id=? AND course_id=? AND status IN ('queued','running')",
                                 (user_id, course_id)).fetchone()
            if active:
                return active[0]
            con.execute("INSERT INTO jobs VALUES (?,?,?,?,?)",
                        (job_id, user_id, course_id, "queued", json.dumps({"progress": 0, "message": "Waiting to import"})))
        executor.submit(ingest_course, user_id, course_id, job_id)
    finally:
        lock.release()
    return job_id


@app.get("/health")
def health():
    return {"status": "ok", "version": "1.2.0", "answer_mode": "local_evidence", "max_download_mb": settings().max_download_mb,
            "source_types": ["drive", "youtube", "web", "post"]}


@app.get("/auth/google/start")
def login():
    url, binding = begin_login()
    response = RedirectResponse(url, status_code=303)
    response.set_cookie("classmind_oauth", binding, httponly=True, secure=settings().cookie_secure,
                        samesite="lax", max_age=600, path="/")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/auth/google/callback")
def callback(request: Request, state: str = "", code: str = "", error: str = ""):
    if error or not code:
        raise ServiceError("Google sign-in was cancelled or permissions were denied. Return to ClassMind and connect again.")
    try:
        user_id = finish_login(state, request.cookies.get("classmind_oauth", ""), code)
    except ServiceError:
        raise
    except Exception as exc:
        # Exception type/location is useful for debugging; never expose token responses.
        import traceback
        from pathlib import Path
        location = traceback.extract_tb(exc.__traceback__)[-1]
        log.error("Google OAuth callback failed (%s at %s:%s)", type(exc).__name__, Path(location.filename).name, location.lineno)
        raise ServiceError("Google sign-in could not finish. Restart the backend and begin a fresh login. If it fails again, check the backend's 'Google OAuth callback failed' diagnostic. [oauth:callback_failed]", 401) from exc
    # Rotate a previous session on this browser.
    old = request.cookies.get("classmind_session")
    if old:
        with db() as con:
            con.execute("DELETE FROM sessions WHERE hash=?", (digest(old),))
    token = create_session(user_id)
    response = RedirectResponse(settings().streamlit_url, status_code=303)
    response.set_cookie("classmind_session", token, httponly=True, secure=settings().cookie_secure,
                        samesite="lax", max_age=settings().session_days * 86400, path="/")
    response.delete_cookie("classmind_oauth", path="/")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.get("/me")
def me(user_id=Depends(current_user)):
    profile, _ = get_user(user_id)
    return profile


@app.post("/auth/logout")
def logout(user_id=Depends(current_user), authorization: str = Header()):
    with db() as con:
        con.execute("DELETE FROM sessions WHERE hash=? AND user_id=?", (digest(authorization[7:]), user_id))
    return {"ok": True}


def course_summaries(user_id):
    with db() as con:
        rows = con.execute("SELECT payload FROM courses WHERE user_id=?", (user_id,)).fetchall()
        jobs = con.execute("""SELECT id,course_id,status,json_remove(payload,'$.files') AS payload
            FROM jobs WHERE rowid IN (SELECT MAX(rowid) FROM jobs WHERE user_id=? GROUP BY course_id)""", (user_id,)).fetchall()
        stats = con.execute("""SELECT course_id,
            json_extract(payload,'$.chunks') AS chunks, json_extract(payload,'$.synced_at') AS synced_at,
            (SELECT count(*) FROM json_each(manifests.payload,'$.files')
             WHERE json_extract(value,'$.status')='imported') AS documents,
            (SELECT count(*) FROM json_each(manifests.payload,'$.files')
             WHERE json_extract(value,'$.status') IN ('skipped','failed')) AS issues,
            (SELECT count(*) FROM json_each(manifests.payload,'$.files')
             WHERE json_extract(value,'$.status')='imported' AND json_extract(value,'$.source_type')='youtube') AS videos
            FROM manifests WHERE user_id=?""", (user_id,)).fetchall()
    counts = {row["course_id"]: dict(row) for row in stats}
    latest = {}
    for row in jobs:
        latest.setdefault(row["course_id"], {"id": row["id"], "status": row["status"], **json.loads(row["payload"])})
    result = []
    for row in rows:
        course = json.loads(row[0])
        data = counts.get(course["id"], {})
        result.append({"id": course["id"], "name": course["name"], "section": course.get("section", ""),
                       "documents": data.get("documents", 0),
                       "issues": data.get("issues", 0), "videos": data.get("videos", 0),
                       "chunks": data.get("chunks") or 0, "synced_at": data.get("synced_at"),
                       "job": latest.get(course["id"])})
    return result


@app.get("/courses")
def courses(user_id=Depends(current_user)):
    return course_summaries(user_id)


@app.post("/courses/refresh")
def refresh_courses(user_id=Depends(current_user)):
    courses = fetch_courses(user_id)
    for course in courses:
        if settings().auto_import_courses and not manifest(user_id, course["id"]).get("collection"):
            enqueue_import(user_id, course["id"])
    return course_summaries(user_id)


@app.post("/courses/{course_id}/sync", status_code=202)
def sync_course(course_id: str, user_id=Depends(current_user)):
    require_course(user_id, course_id)
    return {"job_id": enqueue_import(user_id, course_id)}


@app.get("/jobs/{job_id}")
def job_status(job_id: str, user_id=Depends(current_user)):
    with db() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=? AND user_id=?", (job_id, user_id)).fetchone()
    if not row:
        raise ServiceError("Import job not found.", 404)
    return {"id": row["id"], "status": row["status"], **json.loads(row["payload"])}


@app.get("/courses/{course_id}/materials")
def materials(course_id: str, q: str = "", user_id=Depends(current_user)):
    require_course(user_id, course_id)
    data = manifest(user_id, course_id)
    term = q.casefold().strip()
    files = [f for f in data["files"] if not term or term in " ".join(
        [f.get("file_name", ""), f.get("material_title", ""), *f.get("topics", [])]).casefold()]
    return {"files": files, "chunks": data.get("chunks", 0), "synced_at": data.get("synced_at")}


class SourceRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str = Field(default="", max_length=300)
    transcript: str = Field(default="", max_length=1_000_000)


@app.post("/courses/{course_id}/sources", status_code=202)
def add_source(course_id: str, body: SourceRequest, user_id=Depends(current_user)):
    from backend.classroom.source_types import link_source
    require_course(user_id, course_id)
    try:
        item = link_source(body.url.strip(), body.title.strip(), "added by you")
    except ValueError as exc:
        raise ServiceError(str(exc), 422) from exc
    if body.transcript and len(body.transcript.strip()) < 30:
        raise ServiceError("Provide at least 30 characters of transcript text.", 422)
    item.update(material_title=body.title.strip() or item["file_name"], transcript=body.transcript.strip())
    lock = course_lock(user_id, course_id)
    if not lock.acquire(blocking=False):
        raise ServiceError("This course is importing. Add the link once the current import finishes.", 409)
    try:
        save_extra_source(user_id, course_id, item)
        job_id = enqueue_import(user_id, course_id)
    finally:
        lock.release()
    return {"job_id": job_id, "source_id": item["id"]}


@app.delete("/courses/{course_id}/knowledge-base")
def delete_course(course_id: str, user_id=Depends(current_user)):
    require_course(user_id, course_id)
    lock = course_lock(user_id, course_id)
    if not lock.acquire(blocking=False):
        raise ServiceError("This course is busy. Try again when the current operation finishes.", 409)
    try:
        delete_knowledge_base(user_id, course_id)
    finally:
        lock.release()
    return {"ok": True}


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    mode: Literal["chat", "study", "practice"] = "chat"


@app.get("/courses/{course_id}/history")
def chat_history(course_id: str, user_id=Depends(current_user)):
    require_course(user_id, course_id)
    return history(user_id, course_id)


@app.post("/courses/{course_id}/chat")
def chat(course_id: str, body: ChatRequest, user_id=Depends(current_user)):
    require_course(user_id, course_id)
    if not body.question.strip():
        raise ServiceError("Please enter a question.", 422)
    lock = course_lock(user_id, course_id)
    if not lock.acquire(blocking=False):
        raise ServiceError("This course is busy importing or answering. Please try again shortly.", 409)
    try:
        result = answer_question(user_id, course_id, body.question, recent_history(user_id, course_id), body.mode)
        append_exchange(user_id, course_id, body.question, result)
        return result
    finally:
        lock.release()
