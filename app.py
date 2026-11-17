"""ClassMind: a lightweight, session-cached study workspace."""
import json
from datetime import datetime
from urllib.parse import quote, urlparse
import streamlit as st
from backend.config import settings
from ui_client import APIError, BackendClient
from ui_style import badge, brand, escape_markdown, install_style, page_header

st.set_page_config(page_title="ClassMind", page_icon=":books:", layout="wide")
install_style()
TYPE_LABELS = {"drive": "Document", "youtube": "YouTube", "web": "Web link", "post": "Classroom post", "course": "Course details"}


def client():
    token = st.session_state.get("token", "")
    connection = st.session_state.get("api_client")
    if connection is None or connection.token != token:
        if connection:
            connection.close()
        connection = BackendClient(settings().backend_url, token)
        st.session_state["api_client"] = connection
    return connection


def api(method, path, **kwargs):
    return client().request(method, path, **kwargs)


def courses_list():
    return api("GET", "/courses", ttl=30)


def course_path(course_id):
    return "/courses/" + quote(course_id, safe="")


def source_url(source):
    url = source.get("source_url", "")
    if not url and source.get("source_type", "drive") == "drive":
        url = "https://drive.google.com/file/d/" + quote(source.get("file_id", source.get("id", "")), safe="") + "/view"
    try:
        parsed = urlparse(url)
        return url if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else None
    except ValueError:
        return None


def is_busy(course):
    return bool(course.get("job") and course["job"]["status"] in {"queued", "running"})


def location_label(source):
    if source.get("timestamp") is not None:
        seconds = int(source["timestamp"])
        return f"{seconds // 60}:{seconds % 60:02d} in video"
    if source.get("section"):
        return "Sheet: " + source["section"]
    return f"Page {source['page']}" if source.get("page") else TYPE_LABELS.get(source.get("source_type", "drive"), "Source")


def render_answer(message):
    def references(ids):
        return " " + " ".join("**[" + escape_markdown(sid) + "]**" for sid in ids)
    if message.get("answer"):
        st.markdown(escape_markdown(message["answer"]) + references(message.get("answer_source_ids", ["S1"])))
        for point in message.get("key_points", []):
            st.markdown("- " + escape_markdown(point["text"]) + references(point.get("source_ids", [point.get("source_id", "S1")])))
    else:
        st.markdown(escape_markdown(message["content"]))
    if message.get("guidance"):
        st.info(message["guidance"])
    if message.get("provider_notice"):
        st.warning(message["provider_notice"])
    sources = message.get("sources", [])
    if sources:
        with st.expander(f"View {len(sources)} reference{'s' if len(sources) != 1 else ''}"):
            for source in sources:
                st.markdown(f"**[{escape_markdown(source['id'])}] {escape_markdown(source['file_name'])}**")
                st.caption(location_label(source))
                for excerpt in source.get("quotes", []):
                    st.markdown(escape_markdown(" ".join(excerpt.split())))
                url = source_url(source)
                if url:
                    st.link_button("Open source", url)
    if message.get("elapsed_ms") is not None:
        engine = "Gemini" if message.get("answer_mode") == "gemini" else "Local evidence"
        st.caption(f"{engine} · {message['elapsed_ms'] / 1000:.2f}s")


def watch_imports(initial):
    """Only this small sidebar panel polls; idle pages have no timer."""
    before = [(c["id"], (c.get("job") or {}).get("status")) for c in initial]

    @st.fragment(run_every="5s")
    def progress():
        current = api("GET", "/courses", ttl=30, refresh=True)
        after = [(c["id"], (c.get("job") or {}).get("status")) for c in current]
        if before != after:
            client().invalidate()
            st.rerun()
        active = [c for c in current if is_busy(c)]
        st.caption("IMPORTS IN PROGRESS")
        for course in active[:3]:
            st.write(course["name"])
            job = course["job"]
            st.progress(max(0.0, min(1.0, float(job.get("progress", 0)))), text=job.get("message", "Waiting…"))
        if len(active) > 3:
            st.caption(f"{len(active) - 3} more courses in the queue")
    progress()


def dashboard():
    page_header("WORKSPACE", "Your courses", "Choose a course and pick up where you left off.")
    courses = courses_list()
    a, b, c = st.columns(3)
    a.metric("Courses", len(courses))
    b.metric("Ready to study", sum(bool(course["chunks"]) for course in courses))
    c.metric("Sources", sum(course["documents"] for course in courses))
    st.write("")
    left, right = st.columns([3, 1])
    search = left.text_input("Find a course", placeholder="Search courses", label_visibility="collapsed")
    status = right.selectbox("Course status", ["All courses", "Ready", "Not imported", "Needs attention"], label_visibility="collapsed")
    filtered = [c for c in courses if search.casefold() in c["name"].casefold()]
    if status == "Ready":
        filtered = [c for c in filtered if c["chunks"]]
    elif status == "Not imported":
        filtered = [c for c in filtered if not c["chunks"]]
    elif status == "Needs attention":
        filtered = [c for c in filtered if c.get("issues") or (c.get("job") or {}).get("status") == "failed"]
    pages = max(1, (len(filtered) + 5) // 6)
    page = st.selectbox("Course page", range(1, pages + 1), format_func=lambda p: f"Page {p} of {pages}") if pages > 1 else 1
    for course in filtered[(page - 1) * 6:page * 6]:
        with st.container(border=True):
            name, chat, library = st.columns([5, 1.3, 1.2], vertical_alignment="center")
            with name:
                st.markdown("**" + escape_markdown(course["name"]) + "**")
                state = "Importing" if is_busy(course) else "Ready" if course["chunks"] else "Not imported"
                details = [state, f"{course['documents']} sources"]
                if course.get("section"):
                    details.append(course["section"])
                st.caption(" · ".join(details))
            if chat.button("Open chat", key="open_" + course["id"], type="primary", use_container_width=True):
                st.session_state["selected_course"] = course["id"]
                st.switch_page(chat_page)
            if library.button("Sources", key="sources_" + course["id"], use_container_width=True):
                st.session_state["selected_course"] = course["id"]
                st.switch_page(materials_page)
    if not filtered:
        st.info("No courses found. Try another search or refresh your Classroom course list.")
    with st.expander("Course list options"):
        st.caption("Refresh enrollments from Google Classroom. Manage source imports inside each course's Library.")
        if st.button("Refresh Classroom"):
            with st.spinner("Checking Google Classroom…"):
                api("POST", "/courses/refresh")
            st.rerun()


def select_course():
    courses = courses_list()
    if not courses:
        st.info("Open Courses and refresh Classroom to find your enrollments.")
        st.stop()
    mapping = {c["id"]: c for c in courses}
    ids, selected = list(mapping), st.session_state.get("selected_course")
    course_id = st.selectbox("Course", ids, index=ids.index(selected) if selected in ids else 0, format_func=lambda cid: mapping[cid]["name"])
    st.session_state["selected_course"] = course_id
    return mapping[course_id]


def course_chat():
    page_header("STUDY", "Study chat", "Ask a focused question. Check the sources behind the answer.")
    course = select_course()
    path, busy = course_path(course["id"]), is_busy(course)
    limit_key = "history_limit_" + course["id"]
    limit = st.session_state.get(limit_key, 20)
    messages = api("GET", path + "/history", params={"limit": limit}, ttl=30)
    st.caption(f"{course['documents']} sources · Answers stay in this course")
    if busy:
        st.info("Import in progress. This course will unlock automatically when it finishes.")
    elif not course["chunks"]:
        st.info("Import sources in the Library to ask about lessons. You can already request a course overview.")
    question, mode = None, "chat"
    with st.expander("Study tools"):
        with st.form("study_tools"):
            topic = st.text_input("Topic", placeholder="For example, gradient descent", max_chars=400)
            tool = st.radio("Activity", ["Course overview", "Find readings", "Practice"], horizontal=True)
            submitted = st.form_submit_button("Start", disabled=busy)
        if submitted:
            if tool == "Course overview":
                question = "Tell me course info"
            elif not topic.strip():
                st.warning("Enter a topic first.")
            elif not course["chunks"]:
                st.warning("Import sources before using this activity.")
            else:
                question, mode = (f"What should I study for {topic}?", "study") if tool == "Find readings" else (f"Generate three practice questions about {topic}", "practice")
    if len(messages) == limit:
        if limit < 200 and st.button("Load earlier messages"):
            st.session_state[limit_key] = min(200, limit + 20)
            st.rerun()
        st.caption("Showing recent messages. The full conversation is available in the export.")
    if not messages:
        with st.container(border=True):
            st.subheader("What would you like to understand?")
            st.write("Try a concept from your notes, or open Study tools for a course overview.")
    for message in messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_answer(message)
            else:
                st.markdown(escape_markdown(message["content"]))
    if messages:
        with st.expander("Export conversation"):
            if st.button("Prepare full export"):
                complete = api("GET", path + "/history", refresh=True)
                st.download_button("Download JSON", json.dumps({"course": course["name"], "messages": complete}, indent=2, ensure_ascii=False), file_name="classmind-chat.json", mime="application/json")
    typed = st.chat_input("Ask about this course…", disabled=busy, max_chars=2000)
    if typed:
        question, mode = typed, "chat"
    if question:
        with st.chat_message("user"):
            st.markdown(escape_markdown(question))
        with st.spinner("Looking through your sources…"):
            api("POST", path + "/chat", json={"question": question, "mode": mode})
        st.rerun()


def add_source(course):
    st.caption("Add a YouTube video, public article, or Google Drive link. Paste a transcript when captions or text are unavailable.")
    with st.form("add_source", clear_on_submit=False):
        url = st.text_input("Source URL", placeholder="https://…")
        title = st.text_input("Title (optional)", max_chars=300)
        transcript = st.text_area("Transcript or source text (optional)", height=120, max_chars=1_000_000)
        upload = st.file_uploader("Upload a transcript (optional)", type=["txt", "srt", "vtt"], help="UTF-8; up to 1 MB.")
        submitted = st.form_submit_button("Add and import", type="primary", disabled=is_busy(course))
    if submitted:
        if upload:
            if upload.size > 1_000_000:
                st.error("Transcript uploads must be smaller than 1 MB.")
                return
            try:
                transcript = upload.getvalue().decode("utf-8-sig")
            except UnicodeDecodeError:
                st.error("Save the transcript as UTF-8 text and try again.")
                return
        api("POST", course_path(course["id"]) + "/sources", json={"url": url, "title": title, "transcript": transcript})
        st.session_state["notice"] = "Source saved. Import progress appears in the sidebar."
        st.rerun()


def materials_browser():
    page_header("SOURCES", "Source library", "Keep the material you study in one place.")
    course = select_course()
    path = course_path(course["id"])
    info, action = st.columns([3, 1])
    info.caption(f"{course['documents']} imported sources" + (" · Updated " + datetime.fromtimestamp(course["synced_at"]).strftime("%d %b") if course.get("synced_at") else ""))
    if action.button("Re-sync sources" if course.get("synced_at") else "Import sources", disabled=is_busy(course), use_container_width=True):
        api("POST", path + "/sync")
        st.rerun()
    job = course.get("job") or {}
    if job.get("status") == "failed":
        st.warning(job.get("message", "The last import failed. Re-sync to retry."))
    view = st.radio("Library view", ["Browse", "Add source", "Help"], horizontal=True, label_visibility="collapsed")
    if view == "Add source":
        add_source(course)
    elif view == "Help":
        st.markdown(f"""**Supported sources**

- Documents: PDF, DOCX, PPTX, XLSX, EPUB, HTML, and UTF-8 text, up to **{settings().max_download_mb} MB**.
- YouTube: available captions with timestamp references. Upload a transcript if captions are unavailable.
- Webpages: readable public articles and direct document links.
- Classroom: post text, material descriptions, and accessible attachments.

Google's native Docs, Slides, and Sheets exports have a separate 10 MB limit. Scans, images, raw audio, and video recordings need text or a transcript. Private and login-only webpages cannot be imported automatically.
""")
    else:
        data = api("GET", path + "/materials", ttl=60)
        left, middle, right = st.columns([3, 1, 1])
        term = left.text_input("Search sources", placeholder="Search title or topic", label_visibility="collapsed").casefold().strip()
        kind = middle.selectbox("Type", ["All types", "Document", "YouTube", "Web link", "Classroom post"], label_visibility="collapsed")
        status = right.selectbox("Status", ["All statuses", "imported", "skipped", "failed"], label_visibility="collapsed")
        files = [f for f in data["files"] if (status == "All statuses" or f["status"] == status) and
                 (kind == "All types" or TYPE_LABELS.get(f.get("source_type", "drive")) == kind) and
                 (not term or term in " ".join([f["file_name"], f.get("material_title", ""), *f.get("topics", [])]).casefold())]
        if files:
            pages = max(1, (len(files) + 24) // 25)
            page = st.selectbox("Source page", range(1, pages + 1), format_func=lambda p: f"Page {p} of {pages}") if pages > 1 else 1
            visible = files[(page - 1) * 25:page * 25]
            st.caption(f"{len(files)} matching sources")
            st.dataframe([{"Source": f["file_name"], "Type": TYPE_LABELS.get(f.get("source_type", "drive"), "Other"), "Status": f["status"].capitalize()} for f in visible], hide_index=True, use_container_width=True)
            index = st.selectbox("Source details", range(len(visible)), format_func=lambda i: visible[i]["file_name"])
            file = visible[index]
            with st.container(border=True):
                st.markdown("**" + escape_markdown(file["file_name"]) + "**")
                badge(file["status"].capitalize(), "ready" if file["status"] == "imported" else "wait")
                if file.get("reason"):
                    st.warning(file["reason"])
                for warning in file.get("warnings", []):
                    st.info(warning)
                url = source_url(file)
                if url:
                    st.link_button("Open original", url)
        else:
            st.info("No matching sources. Import the course, add a source, or clear the filters.")
        with st.expander("Manage this course's local data"):
            st.caption("Deletes local sources, the index, and saved chat. Google Classroom originals are unaffected.")
            confirm = st.checkbox("Delete this course's local data", key="delete_" + course["id"])
            if st.button("Delete local data", disabled=not confirm or is_busy(course)):
                api("DELETE", path + "/knowledge-base")
                st.rerun()


def main():
    st.session_state.setdefault("token", st.context.cookies.get("classmind_session", ""))
    with st.sidebar:
        brand()
    if not st.session_state["token"]:
        page_header("YOUR STUDY SPACE", "A clearer way to study", "Your Classroom notes and lessons, organized into answers you can check.")
        with st.container(border=True):
            st.subheader("Connect your classroom")
            st.write("Choose a course, import its sources, and ask questions with references.")
            st.link_button("Connect Google Classroom", settings().public_backend_url.rstrip("/") + "/auth/google/start", type="primary")
        st.caption("Read-only Classroom access. Source search stays on this computer. When Gemini is enabled, questions and selected excerpts are sent to Google for explanations.")
        st.stop()
    profile = api("GET", "/me", ttl=30)
    courses = courses_list()
    with st.sidebar:
        st.caption("WORKSPACE")
    navigation = st.navigation([dashboard_page, chat_page, materials_page])
    with st.sidebar:
        st.divider()
        if any(is_busy(c) for c in courses):
            watch_imports(courses)
        st.caption("CONNECTED ACCOUNT")
        st.write(profile.get("name", "Student"))
        st.caption("Gemini explanations · Cited sources" if settings().answer_provider == "gemini" else "Local answers · Cited sources")
        with st.expander("Account"):
            st.caption(profile.get("email", ""))
            if settings().answer_provider == "gemini":
                st.caption("Gemini receives your question and selected source excerpts. Full files and Google login credentials are not sent to the model.")
            if st.button("Refresh workspace"):
                client().invalidate()
                st.rerun()
            if st.button("Sign out"):
                api("POST", "/auth/logout")
                client().close()
                st.session_state.clear()
                st.session_state["token"] = ""
                st.rerun()
    notice = st.session_state.pop("notice", None)
    if notice:
        st.success(notice)
    navigation.run()


dashboard_page = st.Page(dashboard, title="Courses", icon=":material/grid_view:", default=True)
chat_page = st.Page(course_chat, title="Study chat", icon=":material/chat_bubble:")
materials_page = st.Page(materials_browser, title="Source library", icon=":material/library_books:")

try:
    main()
except APIError as exc:
    st.error(str(exc))
    if exc.status == 401:
        st.link_button("Reconnect Google Classroom", settings().public_backend_url.rstrip("/") + "/auth/google/start")
    elif st.button("Retry connection"):
        client().invalidate()
        st.rerun()
