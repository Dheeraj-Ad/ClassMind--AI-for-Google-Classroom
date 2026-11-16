"""ClassMind: focused course overview, grounded chat, and a searchable source library."""
import json
from datetime import datetime
from urllib.parse import quote, urlparse

import requests
import streamlit as st

from backend.config import settings
from ui_style import badge, brand, escape_markdown, install_style, page_header

st.set_page_config(page_title="ClassMind · Your study space", page_icon="📘", layout="wide")
install_style()
TYPE_LABELS = {"drive": "Document", "youtube": "YouTube", "web": "Web link", "post": "Classroom post", "course": "Course details"}


class APIError(Exception):
    pass


def api(method, path, **kwargs):
    try:
        response = requests.request(method, settings().backend_url.rstrip("/") + path,
            headers={"Authorization": "Bearer " + st.session_state.get("token", "")}, timeout=(5, 180), **kwargs)
    except requests.RequestException as exc:
        raise APIError("Cannot reach the backend. Check that it is running, then try again.") from exc
    if not response.ok:
        try:
            detail = response.json().get("detail", "The request could not be completed.")
        except ValueError:
            detail = "The backend returned an unexpected response."
        if response.status_code == 401:
            st.session_state.pop("profile", None)
        raise APIError(str(detail))
    return response.json()


def course_path(course_id):
    return "/courses/" + quote(course_id, safe="")


def source_url(source):
    url = source.get("source_url", "")
    if not url and source.get("source_type", "drive") == "drive":
        url = "https://drive.google.com/file/d/" + quote(source.get("file_id", source.get("id", "")), safe="") + "/view"
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username else None


def location_label(source):
    if source.get("timestamp") is not None:
        seconds = int(source["timestamp"])
        return f"{seconds // 60}:{seconds % 60:02d} in video"
    if source.get("section"):
        return "Sheet: " + source["section"]
    return f"Page {source['page']}" if source.get("page") else TYPE_LABELS.get(source.get("source_type", "drive"), "Source")


def render_sources(sources):
    if not sources:
        return
    with st.expander(f"References · {len(sources)}", expanded=False):
        for source in sources:
            st.markdown(f"**[{escape_markdown(source['id'])}] {escape_markdown(source['file_name'])}**")
            st.caption(location_label(source))
            for excerpt in source.get("quotes", []):
                st.markdown(escape_markdown(" ".join(excerpt.split())))
            url = source_url(source)
            if url:
                st.link_button("Open source ↗", url)
            st.divider()


def render_answer(message):
    if message.get("answer"):
        st.markdown(escape_markdown(message["answer"]) + " **[S1]**")
        points = message.get("key_points", [])
        if points:
            st.markdown("**Supporting details**")
            for point in points:
                st.markdown("- " + escape_markdown(point["text"]) + " **[" + escape_markdown(point["source_id"]) + "]**")
    else:
        st.markdown(escape_markdown(message["content"]))
    if message.get("guidance"):
        st.info(message["guidance"])
    if message.get("elapsed_ms") is not None:
        st.caption(f"Local search · {message['elapsed_ms'] / 1000:.2f} s")
    render_sources(message.get("sources", []))


def render_report(files):
    if not files:
        st.caption("The first source is still being processed.")
        return
    st.dataframe([{"Source": f["file_name"], "Type": TYPE_LABELS.get(f.get("source_type", "drive"), "Other"),
                   "Status": f["status"], "Details": f.get("reason") or "; ".join(f.get("warnings", [])) or "Ready"}
                  for f in files], hide_index=True, use_container_width=True)


def is_busy(course):
    return bool(course.get("job") and course["job"]["status"] in {"queued", "running"})


def dashboard():
    page_header("YOUR WORKSPACE", "Your study desk", "A focused space for every course. Bring your sources together, then ask better questions.")
    if st.button("Refresh Classroom", help="Check for new courses. This does not start a full import."):
        with st.spinner("Checking your active courses…"):
            api("POST", "/courses/refresh")
        st.rerun()
    dashboard_live()


@st.fragment(run_every="10s")
def dashboard_live():
    courses = api("GET", "/courses")
    a, b, c = st.columns(3)
    a.metric("Your courses", len(courses))
    b.metric("Ready to study", sum(bool(course["chunks"]) for course in courses))
    c.metric("Imported sources", sum(course["documents"] for course in courses))
    st.write("")
    left, right = st.columns([3, 1])
    search = left.text_input("Find a course", placeholder="Search your courses…", label_visibility="collapsed", key="course_search")
    status = right.selectbox("Course status", ["All courses", "Ready", "Not imported", "Needs attention"], label_visibility="collapsed")
    filtered = [course for course in courses if search.casefold() in course["name"].casefold()]
    if status == "Ready":
        filtered = [course for course in filtered if course["chunks"]]
    elif status == "Not imported":
        filtered = [course for course in filtered if not course["chunks"]]
    elif status == "Needs attention":
        filtered = [course for course in filtered if course.get("issues") or (course.get("job") or {}).get("status") == "failed"]
    if not filtered:
        st.info("No matching courses. Refresh Classroom if you recently joined a course.")
    pages = max(1, (len(filtered) + 5) // 6)
    page = st.selectbox("Page", list(range(1, pages + 1)), format_func=lambda p: f"{p} of {pages}", key="course_grid_page") if pages > 1 else 1
    visible = filtered[(page - 1) * 6:page * 6]
    for offset in range(0, len(visible), 2):
        for column, course in zip(st.columns(2), visible[offset:offset + 2]):
            with column, st.container(border=True):
                busy, job = is_busy(course), course.get("job")
                badge("IMPORTING" if busy else "READY TO STUDY" if course["chunks"] else "ADD YOUR MATERIAL", "wait" if busy else "ready" if course["chunks"] else "muted")
                st.subheader(course["name"])
                st.caption(course["section"] or "Google Classroom")
                st.write(f"**{course['documents']}** sources · **{course['chunks']}** searchable passages")
                if busy:
                    st.progress(float(job.get("progress", 0)), text=job.get("message", "Waiting to import…"))
                elif job and job["status"] == "failed":
                    st.caption("The last sync needs attention. Open the import report for details.")
                elif course.get("synced_at"):
                    st.caption("Updated " + datetime.fromtimestamp(course["synced_at"]).astimezone().strftime("%d %b, %H:%M"))
                first, second = st.columns(2)
                if first.button("Open study chat", key="open_" + course["id"], type="primary", use_container_width=True):
                    st.session_state["selected_course"] = course["id"]
                    st.switch_page(chat_page)
                if second.button("Re-sync sources" if course.get("synced_at") else "Import sources", key="sync_" + course["id"], disabled=busy, use_container_width=True):
                    api("POST", course_path(course["id"]) + "/sync")
                    st.rerun(scope="fragment")
                if job:
                    with st.expander("Import report" + (f" · {course['issues']} need attention" if course.get("issues") else "")):
                        if job["status"] == "failed":
                            st.warning(job.get("message", "Re-sync this course to retry."))
                        if st.button("View source details", key="report_" + course["id"]):
                            render_report(api("GET", "/jobs/" + job["id"]).get("files", []))
    if courses:
        with st.expander("Manage local course data"):
            ids = [c["id"] for c in courses]
            names = {c["id"]: c["name"] for c in courses}
            selected = st.selectbox("Course to manage", ids, format_func=names.get)
            st.caption("Delete its local sources, search index, and saved chat. Google Classroom originals are unaffected.")
            confirm = st.checkbox("I want to delete this course's local data")
            if st.button("Delete local knowledge base", disabled=not confirm):
                api("DELETE", course_path(selected) + "/knowledge-base")
                st.rerun(scope="fragment")


def select_course():
    courses = api("GET", "/courses")
    if not courses:
        st.info("Refresh Classroom on the Overview page to find your active student courses.")
        st.stop()
    mapping = {course["id"]: course for course in courses}
    ids, selected = list(mapping), st.session_state.get("selected_course")
    course_id = st.selectbox("Your course", ids, index=ids.index(selected) if selected in ids else 0, format_func=lambda cid: mapping[cid]["name"])
    st.session_state["selected_course"] = course_id
    return mapping[course_id]


def course_chat():
    page_header("ASK CLASSMIND", "Understand what you're studying", "Clear answers from your notes, lesson links, and video transcripts—with the evidence one click away.")
    course = select_course()
    path, busy = course_path(course["id"]), is_busy(course)
    messages = api("GET", path + "/history")
    left, right = st.columns([3, 1])
    left.caption(f"{course['documents']} sources in this course · Private, local answering")
    right.download_button("Export conversation", json.dumps({"course": course["name"], "messages": messages}, indent=2, ensure_ascii=False),
                          file_name="classmind-chat.json", mime="application/json", use_container_width=True)
    if busy:
        st.info("This course is importing. You can ask questions when its index is ready.")
    elif not course["chunks"]:
        st.info("Import this course on the Overview page, or add a lesson link in the Library. Course overview questions already work.")
    question, mode = None, "chat"
    with st.expander("Study tools", expanded=not messages):
        topic = st.text_input("Topic", placeholder="Enter a concept covered in your sources", max_chars=400)
        a, b, c = st.columns(3)
        if a.button("Course overview", disabled=busy, use_container_width=True):
            question = "Tell me course info"
        if b.button("Find what to read", disabled=busy or not topic.strip() or not course["chunks"], use_container_width=True):
            question, mode = f"What should I study for {topic}?", "study"
        if c.button("Practice this topic", disabled=busy or not topic.strip() or not course["chunks"], use_container_width=True):
            question, mode = f"Generate three practice questions about {topic}", "practice"
        st.caption("Practice cards require explanatory material. Topic lists and syllabus headings are excluded.")
    if not messages:
        with st.container(border=True):
            st.subheader("Start with a focused question")
            st.write("Ask for a definition, how a process works, or the difference between two concepts in your course.")
            st.caption("Answers use local source passages. If the evidence is missing, ClassMind will say so.")
    for message in messages:
        with st.chat_message(message["role"]):
            if message["role"] == "assistant":
                render_answer(message)
            else:
                st.markdown(escape_markdown(message["content"]))
    typed = st.chat_input("Ask about a concept in this course…", disabled=busy, max_chars=2000)
    if typed:
        question, mode = typed, "chat"
    if question:
        with st.chat_message("user"):
            st.markdown(escape_markdown(question))
        with st.spinner("Finding and comparing explanatory passages…"):
            api("POST", path + "/chat", json={"question": question, "mode": mode})
        st.rerun()


def materials_browser():
    page_header("YOUR SOURCE LIBRARY", "Everything you learn from", "Documents, YouTube captions, webpages, and Classroom posts, organized in one place.")
    course = select_course()
    browse, add, support = st.tabs(["Browse sources", "Add a lesson link", "Import support"])
    with browse:
        left, middle, right = st.columns([3, 1, 1])
        term = left.text_input("Search sources", placeholder="Search titles, filenames, or topic tags…", label_visibility="collapsed")
        kind = middle.selectbox("Source type", ["All types", "Document", "YouTube", "Web link", "Classroom post"], label_visibility="collapsed")
        status = right.selectbox("Import status", ["All statuses", "imported", "skipped", "failed"], label_visibility="collapsed")
        data = api("GET", course_path(course["id"]) + "/materials", params={"q": term})
        files = [f for f in data["files"] if (status == "All statuses" or f["status"] == status) and
                 (kind == "All types" or TYPE_LABELS.get(f.get("source_type", "drive")) == kind)]
        st.caption(f"{len(files)} matching sources · {data['chunks']} searchable passages in this course")
        if files:
            st.dataframe([{"Source": f["file_name"], "Type": TYPE_LABELS.get(f.get("source_type", "drive"), "Other"),
                           "Status": f["status"].capitalize(), "Passages": f.get("chunks", 0), "Topics": ", ".join(f.get("topics", [])[:4])}
                          for f in files], hide_index=True, use_container_width=True)
            index = st.selectbox("Inspect a source", range(len(files)), format_func=lambda i: files[i]["file_name"])
            file = files[index]
            with st.container(border=True):
                badge(file["status"].upper(), "ready" if file["status"] == "imported" else "wait")
                st.subheader(file["file_name"])
                st.caption(TYPE_LABELS.get(file.get("source_type", "drive"), "Source") + " · " + file.get("origin", "Classroom"))
                if file.get("reason"):
                    st.warning(file["reason"])
                for warning in file.get("warnings", []):
                    st.info(warning)
                if file.get("topics"):
                    st.markdown("**Topics:** " + escape_markdown(" · ".join(file["topics"])))
                url = source_url(file)
                if url:
                    st.link_button("Open original source ↗", url)
        else:
            st.info("No matching sources. Import this course or add a lesson link below.")
    with add:
        st.subheader("Bring another lesson into this course")
        st.write("Add a YouTube video, a public article, or a direct document link. It will be included when this course syncs.")
        with st.form("add_source"):
            url = st.text_input("Lesson URL", placeholder="https://www.youtube.com/watch?v=…")
            title = st.text_input("Title (optional)", max_chars=300)
            transcript = st.text_area("Transcript or source text (optional)", placeholder="If captions or the webpage are unavailable, paste text you have access to.", height=140)
            upload = st.file_uploader("Or upload a transcript", type=["txt", "srt", "vtt"], help="UTF-8 text, SRT, or VTT; up to 1 MB.")
            submitted = st.form_submit_button("Add source and sync course", type="primary", disabled=is_busy(course))
        if submitted:
            if upload:
                if upload.size > 1_000_000:
                    st.error("Transcript uploads must be smaller than 1 MB.")
                    st.stop()
                try:
                    transcript = upload.getvalue().decode("utf-8-sig")
                except UnicodeDecodeError:
                    st.error("Save the transcript as UTF-8 text and upload it again.")
                    st.stop()
            api("POST", course_path(course["id"]) + "/sources", json={"url": url, "title": title, "transcript": transcript})
            st.success("Source saved. Import is queued; follow its progress on the Overview page.")
    with support:
        st.subheader("What can ClassMind import?")
        st.dataframe([
            {"Source": "Drive documents", "Support": f"PDF, DOCX, PPTX, XLSX, EPUB, HTML and text; up to {settings().max_download_mb} MB per file."},
            {"Source": "Google Docs / Slides / Sheets", "Support": "Exported through Google Drive. Google's separate 10 MB export limit still applies."},
            {"Source": "YouTube", "Support": "Available manual or automatic captions, with timestamp citations. Add your own transcript if captions are blocked or unavailable."},
            {"Source": "Public webpages", "Support": "Readable HTML and linked documents. Login-only, private-network and JavaScript-only pages are not supported."},
            {"Source": "Classroom posts", "Support": "Material descriptions, assignment descriptions and announcement text."},
            {"Source": "Images / audio / raw video", "Support": "Attach text or a transcript. OCR and speech-to-text are not enabled."},
        ], hide_index=True, use_container_width=True)
        st.caption("Larger files can take longer. Very large text extractions are capped and reported with a warning; imports never bypass access restrictions.")


def main():
    st.session_state.setdefault("token", st.context.cookies.get("classmind_session", ""))
    with st.sidebar:
        brand()
    if not st.session_state["token"]:
        st.markdown('<div class="cm-hero"><div class="cm-eyebrow">LESS SEARCHING. MORE UNDERSTANDING.</div><h1>A clearer way to study.</h1><p>Your Classroom notes, lesson videos, and useful links. One organized workspace, with answers you can trace back to the source.</p></div>', unsafe_allow_html=True)
        # A native title also keeps the landing page accessible to assistive technology.
        st.subheader("Build your own study space")
        st.link_button("Connect Google Classroom", settings().public_backend_url.rstrip("/") + "/auth/google/start", type="primary")
        st.write("")
        for column, number, title, description in zip(st.columns(3), ["01", "02", "03"], ["Collect your sources", "Ask with context", "Practice with evidence"],
                ["Import documents, videos with captions, and readable lesson links.", "Find complete explanations in the material for your selected course.", "Turn useful passages into study cards and inspect every reference."]):
            with column, st.container(border=True):
                badge(number)
                st.subheader(title)
                st.write(description)
        st.caption("Google sign-in is read-only. Your imported material and AI search stay on this computer; source downloads need an internet connection.")
        st.stop()
    if "profile" not in st.session_state:
        try:
            st.session_state["profile"] = api("GET", "/me")
        except APIError as exc:
            st.error(str(exc))
            st.link_button("Reconnect Google Classroom", settings().public_backend_url.rstrip("/") + "/auth/google/start")
            st.stop()
    with st.sidebar:
        badge("LOCAL AI", "ready")
        st.write(st.session_state["profile"].get("name", "Student"))
        st.caption(st.session_state["profile"].get("email", ""))
        st.divider()
        st.caption("Each course has its own sources and conversation. Answers are grounded in imported material.")
        if st.button("Sign out", use_container_width=True):
            api("POST", "/auth/logout")
            st.session_state.clear()
            st.session_state["token"] = ""
            st.rerun()
    if not st.session_state.get("courses_loaded"):
        with st.spinner("Loading your courses…"):
            if not api("GET", "/courses"):
                api("POST", "/courses/refresh")
        st.session_state["courses_loaded"] = True
    st.navigation([dashboard_page, chat_page, materials_page]).run()


dashboard_page = st.Page(dashboard, title="Overview", icon=":material/dashboard:", default=True)
chat_page = st.Page(course_chat, title="Ask ClassMind", icon=":material/chat_bubble:")
materials_page = st.Page(materials_browser, title="Source Library", icon=":material/library_books:")

try:
    main()
except APIError as exc:
    st.error(str(exc))
    if st.button("Try again"):
        st.rerun()
