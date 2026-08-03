from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import query as query_mod
from ..db import SessionLocal, get_db
from ..models import Chat, Message
from ..schemas import QueryRequest

router = APIRouter(prefix="/api/query", tags=["query"])


@router.post("/stream")
def query_stream(payload: QueryRequest, db: Session = Depends(get_db)):
    chat = db.get(Chat, payload.chat_id) if payload.chat_id else None
    if chat is None:
        chat = Chat(
            title="Neuer Chat",
            selected_collections=json.dumps(payload.collection_ids),
        )
        db.add(chat)
        db.commit()
    else:
        # Remember the selection so reopening the chat restores it.
        chat.selected_collections = json.dumps(payload.collection_ids)
        db.commit()

    # Persist the question first so it survives a failing generation.
    db.add(Message(chat_id=chat.id, role="user", content=payload.question))
    db.commit()

    try:
        ctx = query_mod.prepare_context(db, chat.id, payload.question, payload.collection_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    async def generator():
        # The request-scoped session ends when the response starts streaming,
        # so the orchestration gets its own session for its whole lifetime.
        session = SessionLocal()
        try:
            ctx.collections = [
                session.merge(c, load=True) for c in ctx.collections
            ]
            yield f"event: chat\ndata: {json.dumps({'chat_id': chat.id})}\n\n"
            async for frame in query_mod.run_query(session, ctx):
                yield frame
        finally:
            session.close()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
