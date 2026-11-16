"""Local MiniLM inference using ONNX and the native Rust tokenizer, without PyTorch."""
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from backend.config import settings
from backend.utils.errors import ServiceError

MODEL_FILE = "model_quint8_avx2.onnx"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


class LocalTokenizer:
    def __init__(self, path: Path):
        self.raw = Tokenizer.from_file(str(path))
        self.raw.no_truncation()
        self.raw.no_padding()

    def encode(self, text, add_special_tokens=False, **kwargs):
        return self.raw.encode(text, add_special_tokens=add_special_tokens).ids

    def decode(self, ids, skip_special_tokens=True):
        return self.raw.decode(ids, skip_special_tokens=skip_special_tokens)


class LocalEmbeddingModel:
    max_seq_length = 256

    def __init__(self):
        config = settings()
        if config.embedding_model != "sentence-transformers/all-MiniLM-L6-v2":
            raise ServiceError("Local evidence mode requires the configured all-MiniLM-L6-v2 model.", 503)
        folder = config.storage / "models" / "minilm-onnx"
        if not (folder / "onnx" / MODEL_FILE).is_file() or not (folder / "tokenizer.json").is_file():
            raise ServiceError("Prepare the local AI once: run python scripts/prepare_models.py, then restart the backend.", 503)
        self.tokenizer = LocalTokenizer(folder / "tokenizer.json")
        self.batch_tokenizer = Tokenizer.from_file(str(folder / "tokenizer.json"))
        self.batch_tokenizer.enable_truncation(max_length=self.max_seq_length)
        self.batch_tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = config.embedding_threads
        options.inter_op_num_threads = 1
        options.log_severity_level = 3
        self.session = ort.InferenceSession(str(folder / "onnx" / MODEL_FILE), sess_options=options, providers=["CPUExecutionProvider"])
        self.inputs = {item.name for item in self.session.get_inputs()}

    def get_sentence_embedding_dimension(self):
        return 384

    def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
        result = []
        for start in range(0, len(texts), 16):
            tokens = self.batch_tokenizer.encode_batch(texts[start:start + 16])
            mask = np.asarray([t.attention_mask for t in tokens], dtype=np.int64)
            inputs = {"input_ids": np.asarray([t.ids for t in tokens], dtype=np.int64),
                      "attention_mask": mask, "token_type_ids": np.asarray([t.type_ids for t in tokens], dtype=np.int64)}
            hidden = self.session.run(None, {k: v for k, v in inputs.items() if k in self.inputs})[0]
            weights = mask[:, :, None].astype(np.float32)
            vectors = (hidden * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
            if normalize_embeddings:
                vectors /= np.maximum(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9)
            result.append(vectors)
        return np.concatenate(result, axis=0) if result else np.empty((0, 384), dtype=np.float32)
