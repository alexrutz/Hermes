"""Filesystem layout for uploads and rendered pages."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from .config import env_config

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def data_root() -> Path:
    root = env_config().data_path
    root.mkdir(parents=True, exist_ok=True)
    return root


def uploads_dir(collection_id: str) -> Path:
    path = data_root() / "uploads" / collection_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def pages_dir(collection_id: str) -> Path:
    path = data_root() / "pages" / collection_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(name: str) -> str:
    cleaned = _SAFE.sub("_", Path(name).name).strip("._") or "datei"
    return cleaned[:200]


def page_paths(collection_id: str, page_id: str, suffix: str) -> tuple[Path, Path]:
    base = pages_dir(collection_id)
    return base / f"{page_id}{suffix}", base / f"{page_id}.b64"


def remove_collection_files(collection_id: str) -> None:
    for path in (data_root() / "uploads" / collection_id, data_root() / "pages" / collection_id):
        shutil.rmtree(path, ignore_errors=True)


def remove_files(paths: list[str]) -> None:
    for raw in paths:
        try:
            Path(raw).unlink(missing_ok=True)
        except OSError:
            pass
