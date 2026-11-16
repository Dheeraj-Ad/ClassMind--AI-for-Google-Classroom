"""Extract page-aware documents; scanned or encrypted PDFs fail explicitly."""
import re
import os
import zipfile
from pathlib import Path

import pymupdf
from langchain_core.documents import Document

from backend.utils.errors import ServiceError
from backend.config import settings


def clean_text(text):
    text = text.replace("\x00", "")
    text = re.sub(r"[^\S\n]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def check_office_archive(path):
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        expanded_limit = min(512, max(256, settings().max_download_mb * 2)) * 1024 * 1024
        if len(entries) > 20000 or sum(e.file_size for e in entries) > expanded_limit:
            raise ServiceError("Office document expands beyond the safe extraction limit.")


def load_documents(path: Path, metadata: dict):
    suffix = path.suffix.lower()
    documents, warnings = [], []
    limit = settings().max_extracted_chars
    if suffix in {".pdf", ".epub"}:
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise ServiceError("Password-protected PDF cannot be extracted.")
            if len(pdf) > 2000:
                raise ServiceError("PDF exceeds the 2,000-page extraction limit.")
            blank, extracted = 0, 0
            for page in pdf:
                text = clean_text(page.get_text("text", sort=True))
                if len(text) < 30:
                    blank += 1
                    continue
                documents.append(Document(page_content=text, metadata={**metadata, "page": page.number + 1}))
                extracted += len(text)
                if extracted >= limit:
                    warnings.append("Text extraction reached its configured character limit; later pages were not indexed.")
                    break
            if blank:
                warnings.append(f"{blank} page(s) had little or no extractable text. Images and scans need OCR; OCR is not enabled.")
    elif suffix in {".docx", ".pptx"}:
        check_office_archive(path)
        import nltk
        local_nltk = str(settings().storage / "nltk")
        if local_nltk not in nltk.data.path:
            nltk.data.path.insert(0, local_nltk)
        # Asset downloads belong to explicit setup, not a student's import request.
        os.environ["AUTO_DOWNLOAD_NLTK"] = "False"
        try:
            nltk.data.find("tokenizers/punkt_tab/english/")
            nltk.data.find("taggers/averaged_perceptron_tagger_eng/")
        except LookupError as exc:
            raise ServiceError("Local extraction resources are missing. Run python scripts/prepare_models.py, then re-sync.") from exc
        if suffix == ".docx":
            from langchain_community.document_loaders import UnstructuredWordDocumentLoader
            loader = UnstructuredWordDocumentLoader(str(path), mode="elements")
        else:
            from langchain_community.document_loaders import UnstructuredPowerPointLoader
            loader = UnstructuredPowerPointLoader(str(path), mode="elements")
        pages = {}
        extracted = 0
        for element in loader.lazy_load():
            page = element.metadata.get("page_number") or 0
            pages.setdefault(page, []).append(element.page_content)
            extracted += len(element.page_content)
            if extracted >= limit:
                warnings.append("Text extraction reached its configured character limit; later content was not indexed.")
                break
        for page, paragraphs in pages.items():
            documents.append(Document(page_content=clean_text("\n".join(paragraphs)), metadata={**metadata, "page": page}))
    elif suffix == ".txt":
        try:
            with path.open(encoding="utf-8-sig") as source:
                text = source.read(limit + 1)
        except UnicodeDecodeError as exc:
            raise ServiceError("Text file is not UTF-8. Convert it to UTF-8 and re-sync.") from exc
        documents = [Document(page_content=clean_text(text), metadata={**metadata, "page": 0})]
    elif suffix == ".html":
        from bs4 import BeautifulSoup
        with path.open("rb") as source:
            soup = BeautifulSoup(source.read(8 * 1024 * 1024), "html.parser")
        for node in soup(["script", "style", "nav", "footer", "header", "form", "noscript", "svg"]):
            node.decompose()
        content = soup.find("article") or soup.find("main") or soup.body or soup
        text = clean_text(content.get_text("\n", strip=True))
        if len(text) < 80:
            raise ServiceError("This webpage has too little readable text; it may require JavaScript or sign-in.")
        documents = [Document(page_content=text, metadata={**metadata, "page": 0})]
    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        check_office_archive(path)
        workbook = load_workbook(path, read_only=True, data_only=True)
        extracted = 0
        try:
            for sheet in workbook:
                rows, start_row = [], 1
                for number, row in enumerate(sheet.iter_rows(values_only=True), 1):
                    line = " | ".join(str(cell) if cell is not None else "" for cell in row)
                    if line.strip(" |"):
                        rows.append(line)
                        extracted += len(line)
                    if len(rows) >= 35 or extracted >= limit:
                        documents.append(Document(page_content=f"Sheet: {sheet.title}\n" + "\n".join(rows),
                            metadata={**metadata, "page": 0, "section": sheet.title, "row": start_row}))
                        rows, start_row = [], number + 1
                    if extracted >= limit or number >= 100000:
                        warnings.append("Spreadsheet extraction reached its row or character limit; later rows were not indexed.")
                        break
                if rows:
                    documents.append(Document(page_content=f"Sheet: {sheet.title}\n" + "\n".join(rows),
                        metadata={**metadata, "page": 0, "section": sheet.title, "row": start_row}))
                if extracted >= limit:
                    break
        finally:
            workbook.close()
    else:
        raise ServiceError("No extractor is available for this file type.")
    bounded, used = [], 0
    for document in documents:
        text = document.page_content[:max(0, limit - used)]
        if len(text) < len(document.page_content) and not any("limit" in w for w in warnings):
            warnings.append("Only part of the document was indexed because it exceeds the text extraction limit.")
        if len(text) >= 30:
            bounded.append(Document(page_content=text, metadata=document.metadata))
        used += len(text)
    documents = bounded
    if not documents:
        raise ServiceError("No usable text found. This may be a scan, image-only file, or empty document.")
    return documents, warnings
