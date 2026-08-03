"""SQLAlchemy data model.

Ordering rule that the whole cache design rests on: page order comes from
``Page.sequence_index`` and from nothing else. Never from filename, never from
``created_at``, never from the database's natural row order. Every query that
reads pages orders by it explicitly.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CollectionStatus(str, enum.Enum):
    pending = "pending"
    ingesting = "ingesting"
    cached = "cached"
    stale = "stale"
    error = "error"


class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")

    # Per-collection render overrides. NULL means "inherit the global setting".
    dpi: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_format: Mapped[str | None] = mapped_column(String(8), nullable=True)
    jpeg_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_edge: Mapped[int | None] = mapped_column(Integer, nullable=True)
    grayscale: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default=CollectionStatus.pending.value)
    status_detail: Mapped[str] = mapped_column(Text, default="")

    # Hash of the exact byte sequence that was warmed into the cache. Compared
    # on every query; a mismatch means the branch is no longer what we cached.
    prefix_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cached_prefix_token_count: Mapped[int] = mapped_column(Integer, default=0)
    token_count: Mapped[int] = mapped_column(Integer, default=0)

    # Rolling record of what the last query actually measured, so the cache
    # tree can colour branches by evidence rather than by assumption.
    last_measured_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_measured_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Monotonic, never reused while the branch is valid.
    next_sequence_index: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    documents: Mapped[list["Document"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )
    pages: Mapped[list["Page"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan"
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    collection_id: Mapped[str] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_path: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), default="application/pdf")
    page_count: Mapped[int] = mapped_column(Integer, default=0)

    # Position of this document's first page inside the collection sequence.
    sequence_start: Mapped[int] = mapped_column(Integer, default=0)

    dpi: Mapped[int] = mapped_column(Integer, default=150)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    collection: Mapped[Collection] = relationship(back_populates="documents")
    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("collection_id", "sequence_index"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    collection_id: Mapped[str] = mapped_column(
        ForeignKey("collections.id", ondelete="CASCADE"), index=True
    )

    #: The single source of truth for prompt order.
    sequence_index: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 1-based page number inside its own document, for citations.
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    image_path: Mapped[str] = mapped_column(Text, nullable=False)
    #: Base64 is written once at ingest and replayed byte-for-byte afterwards.
    base64_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(50), default="image/png")

    width: Mapped[int] = mapped_column(Integer, default=0)
    height: Mapped[int] = mapped_column(Integer, default=0)
    dpi: Mapped[int] = mapped_column(Integer, default=150)
    byte_size: Mapped[int] = mapped_column(Integer, default=0)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    image_hash: Mapped[str] = mapped_column(String(64), default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="pages")
    collection: Mapped[Collection] = relationship(back_populates="pages")


class Chat(Base):
    __tablename__ = "chats"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(300), default="Neuer Chat")
    #: JSON array of collection ids, restored when the chat is reopened.
    selected_collections: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="chat", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    chat_id: Mapped[str] = mapped_column(
        ForeignKey("chats.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    chat: Mapped[Chat] = relationship(back_populates="messages")
    collection_answers: Mapped[list["CollectionAnswer"]] = relationship(
        back_populates="message", cascade="all, delete-orphan"
    )
    synthesis_trace: Mapped["SynthesisTrace | None"] = relationship(
        back_populates="message", cascade="all, delete-orphan", uselist=False
    )


class CollectionAnswer(Base):
    """One per collection per assistant message — the raw per-branch result."""

    __tablename__ = "collection_answers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    collection_id: Mapped[str] = mapped_column(String(32), index=True)
    collection_name: Mapped[str] = mapped_column(String(200), default="")

    answer_text: Mapped[str] = mapped_column(Text, default="")
    reasoning_text: Mapped[str] = mapped_column(Text, default="")

    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_hit_rate: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    message: Mapped[Message] = relationship(back_populates="collection_answers")


class SynthesisTrace(Base):
    __tablename__ = "synthesis_traces"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True, unique=True
    )
    reasoning_text: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ttft_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    message: Mapped[Message] = relationship(back_populates="synthesis_trace")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
