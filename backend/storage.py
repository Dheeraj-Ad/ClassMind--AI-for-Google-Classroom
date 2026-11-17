"""Durable metadata, encrypted OAuth credentials, and hashed application sessions."""
import hashlib
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager

from cryptography.fernet import Fernet

from backend.config import settings


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cipher() -> Fernet:
    key = settings().token_encryption_key
    if not key:
        raise RuntimeError("Set TOKEN_ENCRYPTION_KEY in .env before starting ClassMind.")
    return Fernet(key.encode())


@contextmanager
def db():
    settings().storage.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(settings().storage / "classmind.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    cipher()  # Fail closed; never silently create a replacement encryption key.
    with db() as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, profile TEXT, credentials BLOB);
        CREATE TABLE IF NOT EXISTS sessions (hash TEXT PRIMARY KEY, user_id TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS oauth (state TEXT PRIMARY KEY, binding TEXT, verifier BLOB, expires REAL);
        CREATE TABLE IF NOT EXISTS courses (user_id TEXT, id TEXT, payload TEXT, PRIMARY KEY(user_id,id));
        CREATE TABLE IF NOT EXISTS manifests (user_id TEXT, course_id TEXT, payload TEXT, PRIMARY KEY(user_id,course_id));
        CREATE TABLE IF NOT EXISTS extra_sources (user_id TEXT, course_id TEXT, id TEXT, payload TEXT,
            PRIMARY KEY(user_id,course_id,id));
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, user_id TEXT, course_id TEXT, status TEXT, payload TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_job ON jobs(user_id,course_id)
            WHERE status IN ('queued','running');
        CREATE INDEX IF NOT EXISTS jobs_user_course ON jobs(user_id,course_id);
        CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT, course_id TEXT, payload TEXT);
        CREATE INDEX IF NOT EXISTS messages_user_course ON messages(user_id,course_id,id);
        """)
        # One backend worker is required; interrupted imports retain the last published index.
        con.execute("UPDATE jobs SET status='failed', payload=? WHERE status IN ('queued','running')",
                    (json.dumps({"message": "Backend restarted. Re-sync this course.", "progress": 0}),))
        con.execute("DELETE FROM oauth WHERE expires < ?", (time.time(),))
        con.execute("DELETE FROM sessions WHERE expires < ?", (time.time(),))


def save_user(user_id, profile, credentials):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO users VALUES (?,?,?)",
                    (user_id, json.dumps(profile), cipher().encrypt(credentials.encode())))


def get_user(user_id):
    with db() as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not row:
        raise ValueError("Reconnect Google Classroom.")
    return json.loads(row["profile"]), cipher().decrypt(row["credentials"]).decode()


def create_session(user_id):
    token = secrets.token_urlsafe(48)
    with db() as con:
        con.execute("INSERT INTO sessions VALUES (?,?,?)",
                    (digest(token), user_id, time.time() + settings().session_days * 86400))
    return token


def session_user(token):
    with db() as con:
        row = con.execute("SELECT user_id FROM sessions WHERE hash=? AND expires>?",
                          (digest(token), time.time())).fetchone()
    return row[0] if row else None


def manifest(user_id, course_id):
    with db() as con:
        row = con.execute("SELECT payload FROM manifests WHERE user_id=? AND course_id=?",
                          (user_id, course_id)).fetchone()
    return json.loads(row[0]) if row else {"files": [], "collection": None, "chunks": 0}


def save_manifest(user_id, course_id, value):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO manifests VALUES (?,?,?)",
                    (user_id, course_id, json.dumps(value)))


def extra_sources(user_id, course_id):
    with db() as con:
        rows = con.execute("SELECT payload FROM extra_sources WHERE user_id=? AND course_id=?", (user_id, course_id)).fetchall()
    return [json.loads(row[0]) for row in rows]


def save_extra_source(user_id, course_id, item):
    with db() as con:
        con.execute("INSERT OR REPLACE INTO extra_sources VALUES (?,?,?,?)", (user_id, course_id, item["id"], json.dumps(item)))


def history(user_id, course_id):
    with db() as con:
        rows = con.execute("SELECT payload FROM messages WHERE user_id=? AND course_id=? ORDER BY id",
                           (user_id, course_id)).fetchall()
    return [json.loads(row[0]) for row in rows]


def recent_history(user_id, course_id, limit=6):
    with db() as con:
        rows = con.execute("SELECT payload FROM messages WHERE user_id=? AND course_id=? ORDER BY id DESC LIMIT ?",
                           (user_id, course_id, limit)).fetchall()
    return [json.loads(row[0]) for row in reversed(rows)]


def append_exchange(user_id, course_id, question, answer):
    with db() as con:
        for message in ({"role": "user", "content": question}, {"role": "assistant", **answer}):
            con.execute("INSERT INTO messages(user_id,course_id,payload) VALUES (?,?,?)",
                        (user_id, course_id, json.dumps(message)))
