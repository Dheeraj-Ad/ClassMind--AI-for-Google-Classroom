"""One small live Gemini request using synthetic evidence, never private courses."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.documents import Document
from backend.config import settings
from backend.rag.gemini import GeminiUnavailable, generate_answer


def main():
    question = "Explain how gradient descent improves a model in simple terms."
    docs = [Document(page_content="Gradient descent updates model weights in the negative gradient direction to reduce the loss function. The learning rate controls the size of each update. A learning rate that is too large can overshoot a minimum.")]
    started = time.perf_counter()
    try:
        result = generate_answer(question, question, docs, "chat")
    except GeminiUnavailable as error:
        raise SystemExit(str(error)) from None
    if not result.supported:
        raise SystemExit("Gemini connected but declined the synthetic evidence check.")
    print("Model:", settings().gemini_model)
    print("Validated answer:", result.answer.text)
    print("Valid citation count:", len(result.answer.citations))
    print("Elapsed seconds:", round(time.perf_counter() - started, 2))


if __name__ == "__main__":
    main()
