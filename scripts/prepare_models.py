"""Run once with internet access to prepare local extraction and embedding assets."""
import sys
import os
import shutil
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import nltk
import tiktoken
import requests
from backend.rag.vectorstore import embedding_model
from backend.rag.local_embeddings import MODEL_FILE, MODEL_REVISION
from backend.config import settings

destination = settings().storage / "nltk"
destination.mkdir(parents=True, exist_ok=True)
nltk.data.path.insert(0, str(destination))
for resource in ("punkt_tab", "averaged_perceptron_tagger_eng"):
    category = "tokenizers" if resource == "punkt_tab" else "taggers"
    try:
        nltk.data.find(f"{category}/{resource}/")
        continue
    except LookupError:
        pass
    if not nltk.download(resource, download_dir=str(destination), quiet=True, raise_on_error=True):
        raise RuntimeError(f"Could not download {resource}")
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(settings().storage / "tokenizers"))
tiktoken.get_encoding("cl100k_base")
model_dir = settings().storage / "models" / "minilm-onnx"
if not (model_dir / "onnx" / "model_quint8_avx2.onnx").exists() or not (model_dir / "tokenizer.json").exists():
    print("Downloading the compact local MiniLM model (about 23 MB)...", flush=True)
    for filename in ("tokenizer.json", "onnx/" + MODEL_FILE):
        target = model_dir / filename
        if target.is_file():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        previous = settings().storage / "models" / "models--sentence-transformers--all-MiniLM-L6-v2" / "snapshots" / MODEL_REVISION / filename
        if previous.is_file():
            shutil.copy2(previous, target)
            continue
        url = f"https://huggingface.co/{settings().embedding_model}/resolve/{MODEL_REVISION}/{filename}"
        temporary = target.with_suffix(target.suffix + ".part")
        try:
            with requests.get(url, stream=True, timeout=(15, 45)) as response:
                response.raise_for_status()
                with temporary.open("wb") as output:
                    for block in response.iter_content(1024 * 1024):
                        output.write(block)
            temporary.replace(target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
model = embedding_model()
print(f"Ready: embedding dimension {model.get_sentence_embedding_dimension()}, local extraction assets cached.")
