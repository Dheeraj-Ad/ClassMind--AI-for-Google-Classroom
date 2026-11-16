"""Offline real-loader/model/Chroma smoke check; run after prepare_models.py."""
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import nltk
import pymupdf
from cryptography.fernet import Fernet
from docx import Document as WordDocument
from pptx import Presentation
from openpyxl import Workbook

from backend.config import settings
from backend.rag.ingest import split_documents
from backend.rag.retriever import retrieve
from backend.rag.chain import answer_question
from backend.rag.vectorstore import add_chunks, client, create_collection, delete_collection, embedding_model, embedding_windows
from backend.storage import initialize, save_manifest
from backend.utils.file_processor import load_documents


def main():
    # Load prepared assets before moving all test outputs to an isolated data directory.
    original_storage = settings().storage
    nltk.data.path.insert(0, str(original_storage / "nltk"))
    os.environ["TIKTOKEN_CACHE_DIR"] = str(original_storage / "tokenizers")
    model = embedding_model()
    assert model.get_sentence_embedding_dimension() == 384
    output = ROOT / ".test-runs" / ("smoke-" + uuid.uuid4().hex[:8])
    output.mkdir(parents=True)
    os.environ["DATA_DIR"] = str(output / "data")
    os.environ["TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    settings.cache_clear()
    client.cache_clear()
    initialize()
    phrase = "Gradient descent updates model weights in the negative gradient direction to reduce the loss function."
    pdf_path = output / "lecture.pdf"
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_textbox((50, 50, 500, 300), phrase)
        pdf.save(pdf_path)
    docx_path = output / "lecture.docx"
    word = WordDocument()
    word.add_heading("Gradient descent", 0)
    word.add_paragraph(phrase)
    word.save(docx_path)
    pptx_path = output / "lecture.pptx"
    slides = Presentation()
    slide = slides.slides.add_slide(slides.slide_layouts[1])
    slide.shapes.title.text = "Gradient descent"
    slide.placeholders[1].text = phrase
    slides.save(pptx_path)
    html_path = output / "lecture.html"
    html_path.write_text("<article><h1>Gradient descent</h1><p>" + phrase + "</p></article>", encoding="utf-8")
    xlsx_path = output / "lecture.xlsx"
    workbook = Workbook()
    workbook.active.append(["Gradient descent", phrase])
    workbook.save(xlsx_path)
    all_chunks = []
    for path in (pdf_path, docx_path, pptx_path, html_path, xlsx_path):
        metadata = {"user_id": "smoke-user", "course_id": "math", "course_name": "Math",
                    "file_id": path.name, "file_name": path.name, "material_title": "Lecture"}
        documents, _ = load_documents(path, metadata)
        assert any("reduce the loss function" in " ".join(doc.page_content.split()) for doc in documents), path.name
        all_chunks.extend(split_documents(documents))
        print(f"PASS extraction: {path.suffix}")
    collection = create_collection("smoke-user", "math")
    try:
        add_chunks(collection, all_chunks)
        save_manifest("smoke-user", "math", {"collection": collection.name, "files": [], "chunks": len(all_chunks)})
        matches = retrieve("smoke-user", "math", "How does gradient descent reduce loss?")
        assert matches and all("reduce the loss function" in " ".join(d.page_content.split()) for d in matches)
        assert retrieve("other-user", "math", "gradient descent") == []
        result = answer_question("smoke-user", "math", "How does gradient descent reduce loss?")
        assert result["grounded"] and "reduce the loss function" in result["answer"]
        assert all(quote in doc.page_content for source in result["sources"] for quote in source["quotes"]
                   for doc in all_chunks if doc.metadata["file_id"] == source["file_id"])
        print(f"PASS real local answer and passage reranking: {result['elapsed_ms']} ms")
        long_text = ("This introductory section discusses course organization and lectures. " * 150) + "Zebras have black and white stripes."
        windows = list(embedding_windows(long_text))
        assert len(windows) > 1 and "zebras" in windows[-1].lower()
        assert all(len(model.tokenizer.encode(w, add_special_tokens=True)) <= model.max_seq_length for w in windows)
        print(f"PASS real MiniLM embeddings and persistent Chroma retrieval: {len(all_chunks)} chunks, {len(matches)} matches")
        print("PASS full-text embedding windows and user isolation")
    finally:
        delete_collection(collection.name)


if __name__ == "__main__":
    main()
