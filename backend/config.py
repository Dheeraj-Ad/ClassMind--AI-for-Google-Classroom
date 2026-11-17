"""Environment configuration; paths are relative to the project, never the CWD."""
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")
    token_encryption_key: str = ""
    google_credentials_file: Path = Path("backend/auth/credentials.json")
    google_redirect_uri: str = "http://localhost:8000/auth/google/callback"
    backend_url: str = "http://localhost:8000"
    public_backend_url: str = "http://localhost:8000"
    streamlit_url: str = "http://localhost:8501"
    data_dir: Path = Path("data")
    cookie_secure: bool = False
    session_days: int = Field(7, ge=1, le=30)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_threads: int = Field(2, ge=1, le=8)
    import_workers: int = Field(1, ge=1, le=2)
    auto_import_courses: bool = False
    chunk_tokens: int = Field(900, ge=800, le=1000)
    chunk_overlap: int = Field(150, ge=0, le=300)
    retrieval_k: int = Field(6, ge=1, le=12)
    min_similarity: float = Field(0.32, ge=0, le=1)
    max_download_mb: int = Field(200, ge=1, le=1024)
    max_extracted_chars: int = Field(2_000_000, ge=10000, le=10_000_000)
    youtube_languages: str = "en,hi"
    answer_provider: Literal["local_evidence", "gemini"] = "local_evidence"
    gemini_api_key: SecretStr = Field(default="", exclude=True)
    gemini_model: str = Field("gemini-3.5-flash-lite", pattern=r"^[a-z0-9][a-z0-9.-]{1,100}$")
    gemini_timeout_seconds: int = Field(20, ge=5, le=60)
    gemini_max_output_tokens: int = Field(1400, ge=256, le=4096)

    def path(self, path: Path) -> Path:
        return path if path.is_absolute() else ROOT / path

    @property
    def storage(self) -> Path:
        return self.path(self.data_dir)


@lru_cache
def settings() -> Settings:
    return Settings()
