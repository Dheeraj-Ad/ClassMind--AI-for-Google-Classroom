"""Restart only this checkout's local ClassMind servers, preserving all user data."""
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import time

import psutil
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import settings


def main():
    config = settings()
    if config.backend_url.rstrip("/") != "http://localhost:8000" or config.streamlit_url.rstrip("/") != "http://localhost:8501":
        raise SystemExit("This helper supports the default localhost ports. Restart custom deployments with their existing commands.")
    database = config.storage / "classmind.sqlite3"
    if database.exists():
        with sqlite3.connect(database, timeout=5) as connection:
            count = connection.execute("SELECT count(*) FROM jobs WHERE status='running'").fetchone()[0]
        if count and "--cancel-imports" not in sys.argv:
            raise SystemExit("A course import is running. Let it finish, or use --cancel-imports to stop it while preserving completed indexes.")
    targets = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            if "python" not in (process.info["name"] or "").lower() or Path(process.cwd()).resolve() != ROOT:
                continue
            args = process.cmdline()
            backend = "uvicorn" in args and "backend.main:app" in args
            frontend = "streamlit" in args and "app.py" in args and "run" in args
            if backend or frontend:
                targets.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    for process in targets:
        try:
            process.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(targets, timeout=10)
    if alive:
        raise SystemExit("Some old ClassMind processes are still exiting. Try again shortly.")
    log_dir = config.storage / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    commands = [
        ("backend", [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1", "--no-access-log"], "http://127.0.0.1:8000/health"),
        ("frontend", [sys.executable, "-m", "streamlit", "run", "app.py", "--server.address", "localhost", "--server.port", "8501", "--server.headless", "true"], "http://localhost:8501/_stcore/health"),
    ]
    for label, command, url in commands:
        with (log_dir / (label + ".log")).open("a", encoding="utf-8") as output:
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                       creationflags=flags, start_new_session=os.name != "nt")
        for _ in range(80):
            if process.poll() is not None:
                raise SystemExit(f"{label} did not start; check data/logs/{label}.log.")
            try:
                response = requests.get(url, timeout=1)
                if response.ok:
                    if label == "backend" and response.json().get("answer_mode") != config.answer_provider:
                        raise SystemExit("Another backend is serving this port; configured answer mode does not match.")
                    print(f"{label}: ready", flush=True)
                    break
            except requests.RequestException:
                pass
            time.sleep(0.5)
        else:
            raise SystemExit(f"{label} startup timed out; check data/logs/{label}.log.")
    print(f"ClassMind is running at http://localhost:8501 with {config.answer_provider} answers.")


if __name__ == "__main__":
    main()
