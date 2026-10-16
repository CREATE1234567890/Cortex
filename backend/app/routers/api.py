"""FastAPI 路由聚合。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from .. import library, qa
from ..db import db, get_setting, row_to_dict, set_setting, loads
from ..llm import get_llm_config, llm_ready
from .. import config

router = APIRouter(prefix="/api")


# ---------- 健康检查 ----------

@router.get("/health")
def health():
    return {"status": "ok", "llm_ready": llm_ready()}


# ---------- 文献库 ----------

class ScanReq(BaseModel):
    root: str = ""


@router.post("/library/scan")
def scan(req: ScanReq):
    root = req.root or get_setting("kb_root", config.DEFAULT_KB_ROOT)
    try:
        files = library.scan_folder(root)
    except FileNotFoundError as e:
        raise HTTPException(400, str(e))
    return {"root": root, "total": len(files), "files": files}


class IngestReq(BaseModel):
    paths: list[str]
    incremental: bool = True


@router.post("/library/ingest")
def ingest(req: IngestReq):
    if not req.paths:
        raise HTTPException(400, "paths 为空")
    return library.start_ingest(req.paths, req.incremental)


@router.get("/library/job")
def job():
    return library.get_job()


@router.post("/library/job/cancel")
def cancel_job():
    library.cancel_ingest()
    return {"ok": True}


@router.get("/documents")
def documents():
    return library.list_documents()


@router.delete("/documents/{doc_id}")
def delete_doc(doc_id: str):
    doc = library.get_doc(doc_id)
    if not doc:
        raise HTTPException(404, "文档不存在")
    library.delete_document(doc_id)
    return {"ok": True}


@router.post("/documents/{doc_id}/retry")
def retry_doc(doc_id: str):
    library.retry_document(doc_id)
    from .library import process_document
    ok = process_document(doc_id)
    return {"ok": ok}


@router.get("/documents/{doc_id}/pdf")
def serve_pdf(doc_id: str):
    doc = library.get_doc(doc_id)
    if not doc or not Path(doc["pdf_path"]).exists():
        raise HTTPException(404, "PDF 文件不存在")
    return FileResponse(doc["pdf_path"], media_type="application/pdf")


# ---------- 会话与聊天 ----------

class ChatReq(BaseModel):
    question: str = Field(min_length=1)
    conversation_id: str | None = None
    doc_ids: list[str] | None = None   # 范围限定（可选）


@router.post("/conversations")
def create_conversation():
    return {"id": qa.new_conversation()}


@router.get("/conversations")
def list_conversations():
    with db() as conn:
        rows = conn.execute(
            "SELECT c.*, COUNT(m.id) n_messages FROM conversations c "
            "LEFT JOIN messages m ON m.conversation_id=c.id "
            "GROUP BY c.id ORDER BY c.created_at DESC").fetchall()
        return [row_to_dict(r) for r in rows]


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    with db() as conn:
        conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
    return {"ok": True}


@router.get("/conversations/{conv_id}/messages")
def get_messages(conv_id: str):
    with db() as conn:
        rows = conn.execute(
            "SELECT id, role, content, citations, created_at FROM messages "
            "WHERE conversation_id=? ORDER BY id", (conv_id,)).fetchall()
        return [{"id": r["id"], "role": r["role"], "content": r["content"],
                 "citations": loads(r["citations"], [])} for r in rows]


@router.post("/chat")
def chat_sse(req: ChatReq):
    question = req.question.strip()
    if not question:
        raise HTTPException(400, "问题为空")

    conv_id = req.conversation_id or qa.new_conversation()

    def sse():
        first = True
        try:
            for ev in qa.stream_answer(conv_id, question, req.doc_ids):
                if first:
                    yield f"event: meta\ndata: {json.dumps({'conversation_id': conv_id}, ensure_ascii=False)}\n\n"
                    first = False
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'], ensure_ascii=False)}\n\n"
            qa.maybe_rename_conversation(conv_id, question)
        except Exception as e:
            yield f"event: error\ndata: {json.dumps({'message': f'{type(e).__name__}: {e}'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------- 设置 ----------

class SettingsReq(BaseModel):
    kb_root: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    history_turns: str | None = None


MASK = "********"


@router.get("/settings")
def get_settings():
    return {
        "kb_root": get_setting("kb_root", config.DEFAULT_KB_ROOT),
        "llm_base_url": get_setting("llm_base_url", config.DEFAULT_LLM_BASE_URL),
        "llm_model": get_setting("llm_model", config.DEFAULT_LLM_MODEL),
        "llm_api_key": MASK if get_setting("llm_api_key", "") else "",
        "history_turns": get_setting("history_turns", str(config.HISTORY_TURNS)),
        "llm_ready": llm_ready(),
        "embed_model": config.EMBED_MODEL,
    }


@router.post("/settings")
def update_settings(req: SettingsReq):
    for key, val in req.model_dump().items():
        if val is None or val == "":
            continue
        if key == "llm_api_key" and val == MASK:
            continue  # 未修改
        set_setting(key, val)
    return get_settings()


@router.post("/settings/test-llm")
def test_llm():
    from ..llm import chat_text
    try:
        text = chat_text([{"role": "user", "content": "回复两个字：正常"}], temperature=0)
        return {"ok": True, "reply": text[:50]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
