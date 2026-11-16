# ClassMind — Local Study Assistant

ClassMind connects to Google Classroom, organizes downloadable study materials, and finds cited passages that answer your questions. **Answers, topic tags, reading recommendations, and practice cards now run locally. No Groq, other cloud AI provider, or AI API key is used.** Google access still requires the internet.

The selected mode is **fast local evidence mode**: it uses MiniLM for retrieval and passage reranking, excludes syllabus topic lists, and presents complete source explanations with citations. Citations retain document pages, spreadsheet sheets, or video timestamps. It does not generate new explanations or solve unseen problems. Practice uses definition questions and fill-in-the-blank cards from explanatory material. Retrieval can still miss useful evidence; inspect the citations. Missing evidence returns:

> I couldn't find enough explanatory evidence in your imported sources to answer this question.

## Start this prepared checkout

The virtual environment, Google settings, and encryption key may already be configured. Keep the existing `.env` and `backend/auth/credentials.json`. There is no need to reset Google sign-in or delete imported courses after this update.

In PowerShell, enter the project directory:

```powershell
cd "C:\Users\DHEERAJ\OneDrive\Desktop\aiml\Class-Ai\classmind"
```

Prepare the compact local model once if it has not been prepared:

```powershell
.\.venv\Scripts\python.exe scripts/prepare_models.py
```

Start the backend in one terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

Start the frontend in a second terminal, also inside `classmind`:

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address localhost --server.port 8501
```

Open **http://localhost:8501**. Sign in if needed. Existing imported courses remain searchable. Click **Import sources** on the course you need; importing every course automatically is disabled by default. Use **Re-sync sources** once after this update to include previously skipped YouTube links, webpages, post text, and larger supported files. Add your own lesson URL or transcript under **Source Library → Add a lesson link**.

After a code update, stop each old server with Ctrl+C and start it again. Avoid running duplicate backend processes. Refresh the browser after restarting.

Alternatively, `python scripts/restart_local.py` restarts only this checkout's default localhost servers in the background, with logs under `data/logs/`. It refuses to interrupt a running import unless you explicitly add `--cancel-imports`; interrupted imports must be re-synced, while completed indexes are preserved.

## Why the earlier version was slow

- It queued all 22 active courses immediately and sent a Groq topic-generation request for each imported file.
- Answering needed two remote model calls, or three for follow-up questions. Provider timeouts and retries increased the wait.
- First-time model/framework loading and low available system memory added startup and inference delays.
- Every dashboard poll included full import reports, including reports for historical jobs.
- Re-sync downloaded, extracted, and embedded unchanged files again.

The revised version removes remote AI calls, uses a roughly 23 MB quantized ONNX version of MiniLM, bounds inference batches and threads, starts one import at a time by default, reuses unchanged files and vectors, sends small dashboard summaries, and fetches detailed file reports on demand. Polling happens every ten seconds. Existing float32 MiniLM indexes remain usable with the quantized version of the same model; small score differences are expected, so similarity thresholds still require evaluation.

FastAPI remains Python. The measured dashboard handler was tens of milliseconds rather than the source of multi-second AI waits. Native ONNX inference and the native tokenizer handle the compute-heavy parts. Rewriting the polling handler in Rust would not remove remote-service waits, model loading, or exhausted RAM.

## Clean installation

Use **64-bit Python 3.11 on x86-64 Windows** for the tested AVX2 model. The ONNX path selects the CPU provider and does not require a GPU. This pinned quantized artifact is intended for x86-64 CPUs; ARM installations need a separately validated ONNX artifact.

```powershell
cd classmind
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts/setup_env.py
.\.venv\Scripts\python.exe scripts/prepare_models.py
```

`requirements.txt` pins direct dependencies. `requirements-lock.txt` records resolved transitive versions. No PyTorch, sentence-transformers runtime, Groq SDK, or generative model is needed by the revised runtime. An existing virtual environment may still contain those old packages, but the application does not use them. Model weights still come from `sentence-transformers/all-MiniLM-L6-v2`.

`setup_env.py` creates `.env` only if it is absent, generating a Fernet encryption key without printing it. Keep that key stable; changing it makes stored Google credentials unreadable. The preparation script downloads the ONNX model/tokenizer plus extraction/tokenization resources only when missing. It pins the model revision and places assets under `data/`. Once prepared, course search and answers do not need internet access.

## Google Cloud setup

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create/select a project.
2. Under **APIs & Services → Library**, enable **Google Classroom API** and **Google Drive API**.
3. Configure **Google Auth Platform / OAuth consent screen** with an app name, support email, and developer contact. Use External unless your Workspace organization permits an Internal app.
4. In testing mode, add the student Google account under **Audience → Test users**. School administrators may need to allow the app.
5. Configure the scopes below under **Data Access**. Assignment access is optional; the other scopes are required.
6. Create an **OAuth client ID → Web application**, with this exact authorized redirect URI:

   ```text
   http://localhost:8000/auth/google/callback
   ```

7. Download the client JSON and replace `backend/auth/credentials.json`. Keep the downloaded `web` structure. Do not commit or share this file.
8. Open ClassMind and click **Connect Google Classroom**. Grant the permissions and use the tab Google redirects back to the app.

Required scopes:

```text
openid
https://www.googleapis.com/auth/userinfo.email
https://www.googleapis.com/auth/userinfo.profile
https://www.googleapis.com/auth/classroom.courses.readonly
https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly
https://www.googleapis.com/auth/classroom.announcements.readonly
https://www.googleapis.com/auth/drive.readonly
```

Optional assignment scope:

```text
https://www.googleapis.com/auth/classroom.coursework.me.readonly
```

Declining assignment access does not block login or importing materials and announcement attachments. Assignment attachments are reported as skipped. Reconnect with that permission and re-sync to include them.

The application lists all pages of active courses where the account is enrolled **as a student**. Archived and teaching-only courses are excluded. A Web application OAuth client is required, not Desktop credentials. Use `localhost` consistently in browser URLs; mixing it with `127.0.0.1` breaks shared session cookies.

The Google OAuth flow uses offline refresh tokens, PKCE, expiring browser-bound state, validated identity, encrypted stored Google credentials, and hashed application sessions. In testing mode Google refresh tokens may expire after seven days for these scopes. Google policy or administrator restrictions can require reconnecting. References: [Google web-server OAuth](https://developers.google.com/identity/protocols/oauth2/web-server), [Classroom courses.list](https://developers.google.com/workspace/classroom/reference/rest/v1/courses/list).

## Configuration

| Setting | Default / purpose |
| --- | --- |
| `TOKEN_ENCRYPTION_KEY` | Required; generated by `setup_env.py` |
| `GOOGLE_CREDENTIALS_FILE` | `backend/auth/credentials.json` |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/auth/google/callback` |
| `BACKEND_URL` | Streamlit-to-backend address, default `http://localhost:8000` |
| `PUBLIC_BACKEND_URL` | Browser-visible backend address |
| `STREAMLIT_URL` | `http://localhost:8501` |
| `COOKIE_SECURE` | `false` only for localhost; HTTPS hosting requires `true` |
| `SESSION_DAYS` | 7 |
| `DATA_DIR` | `data`, relative to the project directory |
| `AUTO_IMPORT_COURSES` | `false`; set `true` to queue all new courses on refresh |
| `IMPORT_WORKERS` | 1; maximum 2 |
| `EMBEDDING_THREADS` | 2; bounds ONNX CPU usage |
| `EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` |
| `CHUNK_TOKENS` / `CHUNK_OVERLAP` | 900 / 150 |
| `MIN_SIMILARITY` / `RETRIEVAL_K` | 0.32 cosine cutoff / up to 6 parent excerpts |
| `MAX_DOWNLOAD_MB` | 200 per file; streamed to disk |
| `MAX_EXTRACTED_CHARS` | 2,000,000 per source; truncation is reported |
| `YOUTUBE_LANGUAGES` | `en,hi`; fall back to another available caption language with a warning |

An AI API key is not required or read. Application latency still depends on disk, CPU load, available RAM, course size, and Google download speed. The first question after backend startup includes loading the local model and course index; later questions reuse them.

## Features and behavior

**Overview:** searchable course cards, status filters, pagination, import progress, source counts, and on-demand reports. Use **Refresh Classroom** to check enrollments; **Re-sync sources** refreshes a selected course. The local-data deletion control is grouped separately.

**Ask ClassMind:** a primary answer, supporting passages, expandable references, and local response timing. Course overview questions read actual Classroom metadata. Study tools offer readings and practice. Conversation history stays scoped to the selected course and exports as JSON. Simple follow-ups use the previous user question for search context; assistant messages are never evidence.

**Source Library:** filter by type or status, search titles and tags, inspect warnings, and open original sources. Add a public lesson URL with optional pasted text or a UTF-8 TXT/SRT/VTT transcript up to 1 MB. Added sources belong only to that user and course.

Supported downloads: PDF, DOCX, PPTX, XLSX, EPUB, HTML, UTF-8 TXT/Markdown/CSV/JSON/caption text, and Google Docs/Slides/Sheets/Drawings exported as PDF. Available YouTube captions are imported with timestamps; public webpages and Classroom post descriptions are also indexed. Native YouTube attachments and video links embedded in posts are discovered automatically. Duplicate video URLs are combined. Raw video/audio, images, interactive forms, inaccessible files, and owner-disabled downloads are reported explicitly. Google limits native-file exports to 10 MB independently of the configured 200 MB download limit. [Drive export/download guide](https://developers.google.com/workspace/drive/api/guides/manage-downloads)

YouTube may deny requests or provide no captions; private, removed, or age-restricted videos are not bypassed. The report offers a manual transcript fallback. Public-web imports validate DNS and every redirect, reject private networks, and stream downloads to disk. HTML is limited to 8 MB; JavaScript-only and login-only pages may not yield readable text. These limits protect the low-memory local runtime. Source extraction warnings are visible in the Library.

PDF extraction uses PyMuPDF; Office extraction uses LangChain and Unstructured. Available page/slide metadata is preserved. Scanned-only, encrypted, or empty PDFs are not silently indexed; OCR is not enabled. Complex equations, tables, and diagrams may extract poorly.

## Retrieval and re-sync

Each user/course has a distinct persistent Chroma collection. Queries also filter by user and course metadata. Chunks are approximately 900 tokenizer tokens with 150 overlap and preserve page boundaries. MiniLM has a 256-wordpiece context, so each parent chunk is embedded through overlapping 224-wordpiece windows, avoiding silent truncation of later content. ONNX outputs are attention-mask mean pooled and normalized to 384-dimensional vectors.

The model is the official [quantized MiniLM ONNX artifact](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/main/onnx). [Sentence Transformers' inference documentation](https://sbert.net/docs/sentence_transformer/usage/efficiency.html) describes ONNX acceleration and pooling considerations.

Local answering retrieves matching parents, filters non-explanatory text, and reranks up to 48 candidate passages with MiniLM and query coverage. It displays at most three distinct explanations and preserves the exact supporting quotes. Definition questions favor definitions; limitation questions require limitation evidence. It can abstain on paraphrases with little shared terminology. It does not prove that every excerpt fully answers the question. No remote rewrite, answer-generation, or verification call occurs.

Each re-sync creates a staged collection. For unchanged files, the app rechecks Google access and `modifiedTime`, then copies cached extraction and existing vectors. Changed/new files are downloaded and embedded. Removed or newly inaccessible files are left out of the replacement collection. The manifest switches only after indexing succeeds. Listing/provider/index failures keep the previous published index. Per-file unsupported/extraction failures are reported in a completed partial import. Successful imports remove old local generations.

Tags use bounded local phrase-frequency extraction and occur in the source text. To regenerate tags from previously imported files without re-syncing:

```powershell
.\.venv\Scripts\python.exe scripts/refresh_local_tags.py
```

The script uses a conditional database update so it cannot overwrite a concurrent re-sync. Historical import reports may still describe the previous implementation; the Materials Browser reads the current manifest.

## Validation

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/smoke_test.py
.\.venv\Scripts\python.exe scripts/benchmark_local.py
```

Unit/integration tests cover OAuth scope handling, PKCE, token/session security, user/course isolation, local evidence fidelity, abstention, practice cards, local tags, reduced dashboard payloads, bounded history, Google pagination, extraction warnings, cached-vector reuse, revoked-download handling, failed re-sync preservation, and Streamlit pages. Google HTTP calls are mocked in the test suite.

`smoke_test.py` uses actual PDF/DOCX/PPTX/HTML/XLSX extraction, the real local ONNX model, passage reranking, and persistent Chroma retrieval with synthetic documents. It runs offline after model preparation and writes ignored artifacts under `.test-runs/`. `scripts/check_source_connections.py` separately checks a public YouTube lesson and webpage online without changing a course.

`benchmark_local.py` reads an existing course index and prints model-load time, answer time, dashboard time/payload size, and process memory. It prints no course text, user IDs, or credentials and does not add chat messages.

If pytest cannot access its default temporary directory, create `.test-runs` and pass a fresh `--basetemp=.test-runs/run-name`. Live Google sign-in, school policy, and actual download availability still require manual testing with your account.

## Troubleshooting

| Symptom | Action |
| --- | --- |
| Old Groq error still appears | Restart the backend and frontend so the updated local code loads. `/health` should report version `1.2.0` and `answer_mode: local_evidence`. |
| “Prepare the local AI once” | Run `scripts/prepare_models.py`; the first run needs access to Hugging Face. Subsequent answers are local. |
| Slow computer despite local mode | Check available RAM and other applications. Keep one backend process and one import worker. Avoid importing all courses simultaneously. |
| No courses | Confirm active student enrollment; click Refresh courses. |
| Course has no searchable text | Click Import materials and inspect the file report. Scans need external OCR. |
| Old topic failures | Run `scripts/refresh_local_tags.py`; imported vectors do not need rebuilding. |
| `redirect_uri_mismatch` | Match the registered Web client callback exactly, including scheme, hostname, port, and path. |
| Missing assignment permission | This is optional. Materials/announcements can import; reconnect with assignment access to include assignments. |
| Other missing Google permissions | Reconnect and grant the required scopes. |
| OAuth error | Start a fresh login from ClassMind; do not reload an already-used callback URL. Errors distinguish client, code, scope, network, and identity failures without exposing secrets. |
| Private/disabled-download file | Check student sharing and the owner's download policy. The app does not bypass it. |
| Google `429` | Retries are bounded. Wait before re-syncing; reduce concurrent imports. |
| DOCX/PPTX extraction fails | Run the preparation script and verify the source file opens normally. Legacy DOC/PPT are unsupported. |
| Backend restarted during import | Interrupted jobs are marked failed. Re-sync the affected course; its last published index remains intact. |
| Answer is “not covered” | Check the selected course and imported/extracted text. Use specific terms from the material; quoted evidence mode may abstain on broad or purely inferential questions. |

## Security and deployment

Run one FastAPI worker with persistent storage. SQLite, embedded Chroma, process-local locks, and the import executor are intended for this deployment. Do not run multiple backend instances against the same data directory. Protect the directory, especially when the workspace is inside a synced folder.

Google credentials are encrypted with Fernet and application session tokens are hashed. Downloaded files, extracted text, profiles, vectors, and chat history are not application-encrypted; use appropriate filesystem permissions/encryption and backups. Keep `.env` and Google credentials out of source control. Sign-out revokes the application session, not Google consent or stored course data. Cached material permissions are checked on re-sync, not every chat turn.

For HTTPS hosting, use a shared hostname: Streamlit at `/`, backend exposed through `/api/` with the prefix stripped. Set `PUBLIC_BACKEND_URL=https://your-host/api`, `GOOGLE_REDIRECT_URI=https://your-host/api/auth/google/callback`, `STREAMLIT_URL=https://your-host`, `BACKEND_URL=http://127.0.0.1:8000`, and `COOKIE_SECURE=true`. Register that exact callback with Google. Preserve WebSockets and avoid logging callback query strings. Add deployment access restrictions, quotas, retention policies, and dependency maintenance before public use.

## Layout and API

`app.py` contains the three Streamlit pages. `backend/auth/` handles Google sign-in; `backend/classroom/` handles discovery/downloads; `backend/rag/` handles ingestion, ONNX embeddings, retrieval, and local evidence answers; `backend/storage.py` holds SQLite persistence. `data/raw`, `data/processed`, `data/chroma`, and `data/models` hold local assets. Setup, smoke testing, tag migration, and timing tools are in `scripts/`; automated tests are in `tests/`.

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Version and active local answer mode |
| `GET /auth/google/start`, `GET /auth/google/callback` | Browser OAuth |
| `GET /me`, `POST /auth/logout` | Session profile/revocation |
| `GET /courses`, `POST /courses/refresh` | Compact cached summaries / refresh Google enrollments |
| `POST /courses/{id}/sync`, `GET /jobs/{id}` | Import and detailed progress/report |
| `GET /courses/{id}/materials?q=...` | Inventory and keyword search |
| `POST /courses/{id}/chat` | Local answer; body: `question`, `mode` (`chat`, `study`, `practice`) |
| `GET /courses/{id}/history` | Saved course conversation |
| `DELETE /courses/{id}/knowledge-base` | Delete that user's local course data |

Protected endpoints require an application bearer token; ambient browser cookies alone do not authorize API mutations. Deleting a knowledge base removes its local files, vectors, reports, and chat without changing Google originals.
