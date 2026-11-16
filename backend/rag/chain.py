"""Fast local evidence assistant. No remote LLM, API key, or generated factual claims."""
from collections import Counter
import re
import time

from backend.rag.retriever import retrieve
from backend.rag.vectorstore import encode, encode_query
from backend.utils.prompts import ABSTENTION

STOP_WORDS = set("""a an the this that these those it its they them their is are was were be been being
of to in on for with by from at as and or but if than then into about through during after before
what which who when where why how can could would should will do does did have has had explain
describe tell give show please me my our your i we you study topic course material materials
generate three practice questions answers question answer using only recommend relevant documents
coverage explain should more detail example examples summarize summary reading read understand
find help available also below above following section chapter page lecture notes introduction
compare difference differences between define definition meaning explain advantages disadvantages benefits limitations
college university department professor lecturer semester syllabus sessions competency outcomes weeks module unit
""".split())
FOLLOW_UP = re.compile(r"\b(it|its|this|that|these|those|they|them|previous|above|same|more)\b", re.I)


def normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def terms(text):
    values = {w.casefold() for w in re.findall(r"\b[\w][\w+-]*\b", text) if len(w) > 1 and w.casefold() not in STOP_WORDS}
    return {w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith(("ss", "is", "us")) else w for w in values}


def readable(text):
    return re.sub(r"\s+", " ", text).strip()


def explanatory(text):
    words = re.findall(r"\b[A-Za-z][A-Za-z'-]*\b", text)
    if len(words) < 7 or len(set(w.casefold() for w in words)) / len(words) < 0.35:
        return False
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    headers = sum(bool(re.match(r"(?i)^(week\s*\d|unit\s*\d|module\s*\d|CO\d|PO\d|competency|sessions|course outcomes|syllabus|text ?books|reference books|department of|course code|semester)", line)) for line in lines)
    if headers and headers / len(lines) >= 0.3:
        return False
    # Reject topic lists and cover pages; retain definitions, explanations and equations.
    relations = r"\b(is|are|was|were|means|refers|consists|contains|uses?|used|allows?|enables?|represents?|describes?|measures?|reduces?|updates?|increases?|decreases?|because|therefore|when|if|can|must|has|have|unlike|requires?|provides?|produces?|stores?|exists?|forms?)\b"
    return bool(re.search(relations, text, re.I) or (":" in text and len(words) >= 10) or "=" in text)


def semantic_scores(query, candidates):
    import numpy as np
    if not candidates:
        return []
    vectors = np.asarray(encode([readable(passage) for passage, _ in candidates]), dtype=np.float32)
    return (vectors @ np.asarray(encode_query(query), dtype=np.float32)).tolist()


def resolve_query(question, history):
    question = re.sub(r"(?i)^generate (?:three |3 )?practice questions(?: with answers)? about\s+", "", question)
    question = re.sub(r"(?i),?\s*using only the course materials\.?$", "", question).strip()
    if history and FOLLOW_UP.search(question):
        previous = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        if previous:
            return (previous[:900] + "\n" + question)[:2000]
    return question


def passages(text):
    """Return exact contiguous substrings, retaining original whitespace."""
    for block in re.split(r"\n\s*\n|(?<=[.!?])\s+(?=[A-Z])", text):
        block = block.strip()
        while len(block) > 900:
            end = block.rfind(" ", 250, 900)
            end = end if end > 0 else 900
            part, block = block[:end].strip(), block[end:].strip()
            if len(part) >= 35:
                yield part
        if len(block) >= 35:
            yield block


def select_evidence(documents, query):
    query_terms = terms(query)
    if not query_terms:
        return []
    candidates, seen = [], set()
    for doc in documents:
        for passage in passages(doc.page_content):
            if not explanatory(passage) or normalized(passage) in seen:
                continue
            seen.add(normalized(passage))
            overlap = query_terms & terms(passage)
            coverage = len(overlap) / len(query_terms)
            if not overlap or coverage < 0.4:
                continue
            candidates.append((passage, doc))
    candidates = candidates[:48]
    similarities = semantic_scores(query, candidates)
    ranked = []
    for (passage, doc), semantic in zip(candidates, similarities):
        coverage = len(query_terms & terms(passage)) / len(query_terms)
        if semantic < 0.2 and coverage < 0.8:
            continue
        intent_bonus = 0
        if re.search(r"(?i)\b(what is|what are|define|meaning)\b", query):
            intent_bonus = 0.15 if re.search(r"(?i)\b(is|are|means|refers|defined)\b", passage) else 0
        if re.search(r"(?i)\b(disadvantages|limitations|drawbacks)\b", query) and not re.search(r"(?i)\b(disadvantage|limitation|drawback|cannot|problem|difficult|however|limited)\w*\b", passage):
            continue
        score = coverage * 0.5 + semantic * 0.45 + intent_bonus
        ranked.append((score, passage, doc))
    ranked.sort(key=lambda item: item[0], reverse=True)
    selected, seen = [], set()
    for _, passage, doc in ranked:
        key = normalized(passage)
        if key not in seen and not any(len(terms(passage) & terms(old)) / max(1, len(terms(passage) | terms(old))) > 0.8 for old, _ in selected):
            selected.append((passage, doc))
            seen.add(key)
        if len(selected) == 3:
            break
    return selected


def abstain():
    return {"content": ABSTENTION, "sources": [], "grounded": False, "answer_mode": "local_evidence"}


def source_record(sid, passage, doc):
    return {"id": sid, "file_name": doc.metadata["file_name"], "file_id": doc.metadata["file_id"],
            "page": doc.metadata.get("page", 0), "material_title": doc.metadata.get("material_title", ""), "quotes": [passage],
            "source_type": doc.metadata.get("source_type", "drive"), "source_url": doc.metadata.get("source_url", ""),
            "timestamp": doc.metadata.get("timestamp"), "section": doc.metadata.get("section", "")}


def course_overview(user_id, course_id):
    from backend.classroom.fetch_courses import require_course
    from backend.storage import manifest
    course = require_course(user_id, course_id)
    data = manifest(user_id, course_id)
    imported = [f for f in data["files"] if f.get("status") == "imported"]
    description = course.get("description") or course.get("section") or ""
    answer = course["name"] + (" — " + readable(description) if description else "")
    point = f"Your library contains {len(imported)} imported sources and {data.get('chunks', 0)} searchable passages."
    source = {"id": "S1", "file_name": course["name"], "file_id": course_id, "source_type": "course",
              "source_url": course.get("alternateLink", "https://classroom.google.com/"), "page": 0, "quotes": [answer]}
    return {"content": answer + " [S1]\n\n" + point, "answer": answer, "key_points": [{"text": point, "source_id": "S1"}],
            "sources": [source], "grounded": True, "answer_mode": "course_overview"}


def practice_card(passage, query_terms):
    definition = re.match(r"^((?:A |An |The )?[A-Za-z][A-Za-z -]{2,75}?)\s+(?:is|are|means|refers to)\s+", readable(passage))
    if definition and len(definition.group(1).split()) <= 9:
        return f"How does the material define {definition.group(1)}?", readable(passage)
    candidates = list(re.finditer(r"\b[A-Za-z][A-Za-z-]{3,}\b", passage))
    candidates = [m for m in candidates if m.group().casefold() not in STOP_WORDS]
    if not candidates:
        return "Which passage supports this topic?", passage
    candidates.sort(key=lambda m: (m.group().casefold() in query_terms, len(m.group())), reverse=True)
    match = candidates[0]
    question = passage[:match.start()] + "_____" + passage[match.end():]
    return "Complete this excerpt:\n" + question, match.group()


def answer_question(user_id, course_id, question, history=None, mode="chat"):
    started = time.perf_counter()
    if mode == "chat" and re.search(r"(?i)\b(course info(?:rmation)?|course overview|about (?:this|the|my) course|what is this course)\b", question):
        result = course_overview(user_id, course_id)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
        return result
    query = resolve_query(question, history if mode == "chat" else None)
    documents = retrieve(user_id, course_id, query)
    evidence = select_evidence(documents, query)
    if not evidence:
        result = abstain()
        result["guidance"] = "I could not find an explanatory passage for that question. A syllabus or topic heading alone is not enough. Try a specific topic, or import lesson notes or a video transcript."
    else:
        sections, sources = [], []
        for i, (passage, doc) in enumerate(evidence, 1):
            sid = f"S{i}"
            sources.append(source_record(sid, passage, doc))
            if mode == "practice":
                prompt, answer = practice_card(passage, terms(query))
                sections.append(f"{i}. {readable(prompt)}\nAnswer: {answer} [{sid}]")
            elif mode == "study":
                location = f", page {doc.metadata['page']}" if doc.metadata.get("page") else ""
                sections.append(f"Read {doc.metadata['file_name']}{location}:\n{readable(passage)} [{sid}]")
            else:
                sections.append(f"{readable(passage)} [{sid}]")
        intro = {"chat": "Answer from your course material:", "study": "Start with these readings:",
                 "practice": "Practice cards based on exact course excerpts:"}[mode]
        result = {"content": intro + "\n\n" + "\n\n".join(sections), "sources": sources,
                  "grounded": True, "answer_mode": "local_evidence"}
        if mode == "chat":
            result["answer"] = readable(evidence[0][0])
            result["key_points"] = [{"text": readable(passage), "source_id": f"S{i}"} for i, (passage, _) in enumerate(evidence[1:], 2)]
    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def extract_topics(text):
    """Bounded local phrase-frequency tags; every label occurs in the source text."""
    sample = text[:30000]
    counts, originals = Counter(), {}
    for phrase in re.finditer(r"\b[A-Za-z][A-Za-z-]{2,}(?:[ \t]+[A-Za-z][A-Za-z-]{2,}){0,2}\b", sample):
        words = phrase.group().split()
        for n in (2, 1):
            for i in range(len(words) - n + 1):
                group = words[i:i + n]
                if any(w.casefold() in STOP_WORDS for w in group):
                    continue
                label = " ".join(group)
                if label not in sample:
                    continue
                key = label.casefold()
                counts[key] += 1 + (n - 1) * 0.4
                originals.setdefault(key, label)
    ranked = sorted(counts, key=lambda k: (-counts[k], -len(k), k))
    tags = []
    for key in ranked:
        if any(key in existing.casefold() or existing.casefold() in key for existing in tags):
            continue
        tags.append(originals[key])
        if len(tags) == 5:
            break
    return tags, None
