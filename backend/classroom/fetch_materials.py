"""Discover documents, YouTube videos, public links, and Classroom post text."""
import re
from backend.classroom.fetch_courses import execute, service
from backend.auth.google_auth import ASSIGNMENT_SCOPE, get_credentials, normalized_scopes
from backend.utils.errors import ServiceError
from backend.classroom.source_types import link_source


def fetch_materials(user_id, course_id):
    client = service(user_id, "classroom", "v1")
    sources = [
        ("materials", client.courses().courseWorkMaterials(), "courseWorkMaterial"),
        ("announcements", client.courses().announcements(), "announcements"),
    ]
    files, skipped, warnings = {}, [], []
    credentials = get_credentials(user_id)
    granted = credentials.granted_scopes if credentials.granted_scopes is not None else credentials.scopes
    if ASSIGNMENT_SCOPE in normalized_scopes(granted):
        sources.append(("assignments", client.courses().courseWork(), "courseWork"))
    else:
        skipped.append({"id": "assignment-access-not-granted", "file_name": "Assignment attachments",
                        "status": "skipped", "reason": "Optional assignment permission was not granted. Course materials and announcement attachments are still imported. Reconnect and allow assignment access to include these files."})
    for label, resource, result_key in sources:
        page = None
        try:
            while True:
                result = execute(resource.list(courseId=course_id, pageSize=100, pageToken=page))
                for post in result.get(result_key, []):
                    title = post.get("title") or post.get("text", "Announcement")[:160]
                    body = post.get("description") or post.get("text", "")
                    if len(body.strip()) >= 30:
                        key = f"post:{label}:{post['id']}"
                        files.setdefault(key, {"id": key, "file_name": title or "Classroom post", "material_title": title,
                                               "source_type": "post", "text": body, "origin": label,
                                               "source_url": post.get("alternateLink", "")})
                    attachments = list(post.get("materials", []))
                    attachments.extend({"link": {"url": url.rstrip(".,;)")}} for url in re.findall(r"https?://[^\s<>]+", body))
                    for index, attachment in enumerate(attachments):
                        drive = attachment.get("driveFile", {}).get("driveFile", {})
                        if drive.get("id"):
                            files.setdefault(drive["id"], {
                                "id": drive["id"], "file_name": drive.get("title", title),
                                "material_title": title, "origin": label, "source_type": "drive",
                                "source_url": "https://drive.google.com/file/d/" + drive["id"] + "/view",
                            })
                        elif attachment.get("youtubeVideo", {}).get("id") or attachment.get("link", {}).get("url"):
                            video = attachment.get("youtubeVideo", {})
                            link = attachment.get("link", {})
                            url = "https://www.youtube.com/watch?v=" + video["id"] if video.get("id") else link["url"]
                            try:
                                source = link_source(url, video.get("title") or link.get("title", ""), label)
                                files.setdefault(source["id"], {**source, "material_title": title})
                            except ValueError:
                                skipped.append({"id": f"{label}:{post['id']}:{index}", "file_name": title,
                                                "status": "skipped", "reason": "The attachment URL is invalid or unsupported."})
                        else:
                            skipped.append({"id": f"{label}:{post['id']}:{index}", "file_name": title,
                                            "status": "skipped", "reason": "Interactive forms and Classroom add-ons cannot be extracted as study text."})
                page = result.get("nextPageToken")
                if not page:
                    break
        except ServiceError as exc:
            warnings.append(f"Could not list {label}: {exc.message}")
    return list(files.values()), skipped, warnings
