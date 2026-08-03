"""Environment-backed defaults.

These values only *seed* the settings store on first start. After that the
persisted settings in the database win, because they are editable from the UI.
The one exception is ``DATA_DIR`` and the database URL, which are pure
deployment concerns and never editable at runtime.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- deployment ---------------------------------------------------------
    data_dir: str = "/data"
    cors_origins: str = "*"

    # --- SGLang connection --------------------------------------------------
    sglang_base_url: str = "http://localhost:30000"
    sglang_web_password: str = ""
    sglang_auth_header: str = "Authorization"
    sglang_auth_scheme: str = "Bearer"
    sglang_model: str = "nvidia/Qwen3.6-27B-NVFP4"

    # Prefill of a 200k token prefix genuinely takes minutes on a cold cache.
    sglang_connect_timeout: float = 15.0
    sglang_read_timeout: float = 1800.0
    sglang_max_retries: int = 3
    sglang_retry_backoff: float = 2.0

    # --- context budget -----------------------------------------------------
    max_context_tokens: int = 262144
    reserved_output_tokens: int = 8192

    # --- document conversion ------------------------------------------------
    default_pdf_dpi: int = 150
    default_max_edge: int = 2048
    default_image_format: str = "png"
    default_jpeg_quality: int = 90
    default_grayscale: bool = False

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir)


@lru_cache
def env_config() -> EnvConfig:
    return EnvConfig()


def database_url() -> str:
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    db_path = env_config().data_path / "hermes.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db_path}"
