from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Chat, CollectionAnswer, Message, SynthesisTrace
from ..schemas import ChatCreate, ChatUpdate

router = APIRouter(prefix="/api/chats", tags=["chats"])


def _selected(chat: Chat) -> list[str]:
    try:
        value = json.loads(chat.selected_collections or "[]")
        return value if isinstance(value, list) else []
    except json.JSONDecodeError:
        return []


def _summary(chat: Chat, message_count: int) -> dict:
    return {
        "id": chat.id,
        "title": chat.title,
        "selected_collections": _selected(chat),
        "message_count": message_count,
        "created_at": chat.created_at.isoformat(),
        "updated_at": chat.updated_at.isoformat(),
    }


@router.get("")
def list_chats(q: str = Query(default=""), db: Session = Depends(get_db)):
    query = db.query(Chat)
    if q:
        query = query.filter(Chat.title.ilike(f"%{q}%"))
    chats = query.order_by(Chat.updated_at.desc()).all()
    counts = dict(
        db.query(Message.chat_id, func.count(Message.id)).group_by(Message.chat_id).all()
    )
    return [_summary(chat, counts.get(chat.id, 0)) for chat in chats]


@router.post("", status_code=201)
def create_chat(payload: ChatCreate, db: Session = Depends(get_db)):
    chat = Chat(
        title=payload.title or "Neuer Chat",
        selected_collections=json.dumps(payload.selected_collections),
    )
    db.add(chat)
    db.commit()
    return _summary(chat, 0)


@router.get("/{chat_id}")
def get_chat(chat_id: str, db: Session = Depends(get_db)):
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(404, "Chat nicht gefunden.")
    messages = (
        db.query(Message)
        .filter(Message.chat_id == chat_id)
        .order_by(Message.created_at.asc(), Message.id.asc())
        .all()
    )
    payload = []
    for message in messages:
        answers = (
            db.query(CollectionAnswer)
            .filter(CollectionAnswer.message_id == message.id)
            .order_by(CollectionAnswer.created_at.asc())
            .all()
        )
        trace = (
            db.query(SynthesisTrace).filter(SynthesisTrace.message_id == message.id).first()
        )
        payload.append(
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "created_at": message.created_at.isoformat(),
                "collection_answers": [
                    {
                        "collection_id": a.collection_id,
                        "collection_name": a.collection_name,
                        "answer_text": a.answer_text,
                        "reasoning_text": a.reasoning_text,
                        "latency_ms": a.latency_ms,
                        "ttft_ms": a.ttft_ms,
                        "prompt_tokens": a.prompt_tokens,
                        "cached_tokens": a.cached_tokens,
                        "completion_tokens": a.completion_tokens,
                        "cache_hit_rate": a.cache_hit_rate,
                        "error": a.error,
                    }
                    for a in answers
                ],
                "synthesis": (
                    {
                        "reasoning_text": trace.reasoning_text,
                        "latency_ms": trace.latency_ms,
                        "ttft_ms": trace.ttft_ms,
                        "prompt_tokens": trace.prompt_tokens,
                        "completion_tokens": trace.completion_tokens,
                        "skipped": trace.skipped,
                    }
                    if trace
                    else None
                ),
            }
        )
    return {
        **_summary(chat, len(messages)),
        "messages": payload,
    }


@router.patch("/{chat_id}")
def update_chat(chat_id: str, payload: ChatUpdate, db: Session = Depends(get_db)):
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(404, "Chat nicht gefunden.")
    if payload.title is not None:
        chat.title = payload.title.strip() or "Neuer Chat"
    if payload.selected_collections is not None:
        chat.selected_collections = json.dumps(payload.selected_collections)
    db.commit()
    count = db.query(Message).filter(Message.chat_id == chat_id).count()
    return _summary(chat, count)


@router.delete("/{chat_id}", status_code=204)
def delete_chat(chat_id: str, db: Session = Depends(get_db)):
    chat = db.get(Chat, chat_id)
    if chat is None:
        raise HTTPException(404, "Chat nicht gefunden.")
    db.delete(chat)
    db.commit()
