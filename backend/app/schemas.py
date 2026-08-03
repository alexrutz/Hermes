from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    dpi: int | None = Field(default=None, ge=72, le=300)
    image_format: str | None = None
    jpeg_quality: int | None = Field(default=None, ge=30, le=100)
    max_edge: int | None = Field(default=None, ge=0, le=8192)
    grayscale: bool | None = None


class CollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    dpi: int | None = Field(default=None, ge=72, le=300)
    image_format: str | None = None
    jpeg_quality: int | None = Field(default=None, ge=30, le=100)
    max_edge: int | None = Field(default=None, ge=0, le=8192)
    grayscale: bool | None = None


class ReorderRequest(BaseModel):
    document_ids: list[str]


class ChatCreate(BaseModel):
    title: str = "Neuer Chat"
    selected_collections: list[str] = []


class ChatUpdate(BaseModel):
    title: str | None = None
    selected_collections: list[str] | None = None


class QueryRequest(BaseModel):
    chat_id: str | None = None
    question: str = Field(min_length=1)
    collection_ids: list[str] = Field(min_length=1)


class SettingsUpdate(BaseModel):
    values: dict[str, Any]


class SettingsReset(BaseModel):
    keys: list[str]


class DpiPreviewRequest(BaseModel):
    dpi: int = Field(ge=72, le=300)
    max_edge: int = Field(default=0, ge=0, le=8192)
