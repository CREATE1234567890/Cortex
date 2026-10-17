"""知识库管理：文件夹扫描、批量导入、后台摄取流水线（FR-KB-01/03, FR-DP-01~03, FR-DP-06）。

摄取幂等：同一 sha256 只入库一次；已 indexed 的文档跳过；失败可重试。
嵌入批量化：整个文档的 chunks 一次 encode，减少模型调用开销。
"""
from __future__ import annotations

import hashlib
import multiprocessing
import threading
import time
import uuid
from multiprocessing import get_context
from pathlib import Path

from . import config
from .chunking import chunk_blocks
from .db import db, row_to_dict
from .embedding import embed_passages
from .indexer import get_index
from .parsing.pdf_parser import parse_pdf
from .classify import classify as classify_doc

_scan_lock = threading.Lock()
_ingest_thread: threading.Thread | None = None
_reembed_thread: threading.Thread | None = None
_stop_event = threading.Event()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def scan_folder(root: str) -> list[dict]:
    """递归发现 PDF，返回 [{path, filename, size_mb, known}]。"""
    rootp = Path(root)
    if not rootp.exists():
        raise FileNotFoundError(f"目录不存在: {root}")
    results = []
    with db() as conn:
        known_hashes = {r["file_hash"] for r in conn.execute("SELECT file_hash FROM documents")}
        known_names = {r["filename"] for r in conn.execute("SELECT filename FROM documents")}
    for p in sorted(rootp.rglob("*.pdf")):
        try:
            size_mb = p.stat().st_size / 1e6
        except OSError:
            continue
        results.append({
            "path": str(p),
            "filename": p.name,
            "size_mb": round(size_mb, 2),
            "known": p.name in known_names,  # 按文件名粗判，导入时按 hash 精确去重
        })
    return results


def register_or_get(path: str) -> tuple[str, bool]:
    """注册文档（hash 去重），返回 (doc_id, created)。"""
    p = Path(path)
    file_hash = sha256_file(p)
    with db() as conn:
        row = conn.execute("SELECT id FROM documents WHERE file_hash=?", (file_hash,)).fetchone()
        if row:
            return row["id"], False
        doc_id = f"doc_{uuid.uuid4().hex[:10]}"
        conn.execute(
            "INSERT INTO documents(id, title, filename, pdf_path, file_hash, status) "
            "VALUES (?, ?, ?, ?, ?, 'pending')",
            (doc_id, p.stem, p.name, str(p), file_hash),
        )
    return doc_id, True


def get_doc(doc_id: str) -> dict | None:
    with db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return row_to_dict(row) if row else None


def list_documents() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, filename, year, venue, category, status, n_pages, n_chunks, error, created_at, indexed_at "
            "FROM documents ORDER BY created_at DESC"
        ).fetchall()
        return [row_to_dict(r) for r in rows]


def delete_document(doc_id: str):
    # 向量先于 chunks 行删除；全文索引由 chunks 表的 FTS5 触发器自动清理
    get_index().delete_doc(doc_id)
    with db() as conn:
        conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
        conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))


def retry_document(doc_id: str):
    with db() as conn:
        conn.execute("UPDATE documents SET status='pending', error=NULL WHERE id=?", (doc_id,))


# ---------- 摄取流水线 ----------
# 重活（解析/切块/嵌入）放独立子进程执行：
# 1) 不占主进程 GIL，摄取期间 API/前端保持响应；
# 2) 病理 PDF 卡死时可按超时强制击杀子进程并重建，不拖垮服务。

_DOC_TIMEOUT_S = 300
_ingest_pool = None


def _doc_heavy_worker(pdf_path: str) -> dict:
    """子进程内执行：解析 → 切块 → 嵌入。返回可 pickle 的 payload。"""
    parsed = parse_pdf(pdf_path)
    chunks = chunk_blocks(parsed.blocks)
    if not chunks:
        raise RuntimeError("解析后无有效内容块（可能是扫描件，当前版本不支持 OCR）")
    vectors = embed_passages([c.text for c in chunks])
    return {
        "n_pages": parsed.n_pages,
        "title": parsed.title,
        "year": parsed.year,
        "chunks": [{
            "text": c.text,
            "section_path": c.section_path,
            "page_start": c.page_start,
            "page_end": c.page_end,
            "bboxes": [{"page": b["page"], "bbox": [round(v, 1) for v in b["bbox"]]}
                       for b in c.bboxes],
            "block_type": c.block_type,
        } for c in chunks],
        "vectors": vectors,
    }


def _get_pool():
    global _ingest_pool
    if _ingest_pool is None:
        _ingest_pool = get_context("spawn").Pool(processes=1)
    return _ingest_pool


def _reset_pool():
    global _ingest_pool
    if _ingest_pool is not None:
        try:
            _ingest_pool.terminate()
            _ingest_pool.join()
        except Exception:
            pass
        _ingest_pool = None


def _set_job(**kw):
    sets = ", ".join(f"{k}=?" for k in kw)
    with db() as conn:
        conn.execute(
            f"UPDATE ingest_jobs SET {sets}, updated_at=datetime('now') WHERE id=1",
            list(kw.values()),
        )


def get_job() -> dict:
    with db() as conn:
        row = conn.execute("SELECT * FROM ingest_jobs WHERE id=1").fetchone()
        return row_to_dict(row)


def _valid_title(title: str | None, fallback: str) -> str:
    """清洗解析出的标题：控制字符/公式占位/乱码/过短时退回文件名。"""
    import re as _re
    t = _re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", (title or "")).strip()
    t = _re.sub(r"\s+", " ", t)
    bad = (
        not t
        or "formula omitted" in t.lower()
        or "\ufffd" in t
        or len(t) < 8
    )
    if bad:
        stem = _re.sub(r"\s+", " ", Path(fallback).stem.replace("_", " ").strip())
        return stem[:200] if stem else (t or "未命名文献")
    return t[:200]


def process_document(doc_id: str) -> bool:
    """单个文档：子进程解析/切块/嵌入 → 主进程落库/建索引。返回是否成功。"""
    import json as _json
    doc = get_doc(doc_id)
    if not doc:
        return False
    try:
        with db() as conn:
            conn.execute("UPDATE documents SET status='parsing' WHERE id=?", (doc_id,))

        pool = _get_pool()
        res = pool.apply_async(_doc_heavy_worker, (doc["pdf_path"],))
        try:
            payload = res.get(_DOC_TIMEOUT_S)
        except get_context("spawn").TimeoutError:
            _reset_pool()
            raise RuntimeError(f"处理超时（>{_DOC_TIMEOUT_S}s，疑似病理 PDF），已强制终止子进程")

        chunks = payload["chunks"]
        title = _valid_title(payload["title"], doc["filename"])
        category, venue = classify_doc(
            doc["pdf_path"], config.get_kb_root(), payload["title"] or "")
        # Contextual Retrieval（机械版）：嵌入文本带文档级前缀，提升跨文档区分度
        ctx_head = f"《{title}》"
        if venue:
            ctx_head += f" {venue}"
        if payload["year"]:
            ctx_head += f" {payload['year']}"
        texts = [f"{ctx_head} | {c['text']}" for c in chunks]
        vectors = embed_passages(texts)

        index = get_index()
        # 先按现存 chunk id 清理旧向量，再重写（FTS 由触发器随 chunks 行同步）
        with db() as conn:
            old_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM chunks WHERE doc_id=?", (doc_id,))]
        if old_ids:
            index.remove_ids(set(old_ids))
        with db() as conn:
            conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

        with db() as conn:
            new_ids: list[int] = []
            for i, c in enumerate(chunks):
                cur = conn.execute(
                    "INSERT INTO chunks(doc_id, seq, text, section_path, page_start, page_end, "
                    "char_span, bbox, block_type) VALUES (?,?,?,?,?,?,?,?,?)",
                    (doc_id, i, c["text"], _json.dumps(c["section_path"], ensure_ascii=False),
                     c["page_start"], c["page_end"], "[0,0]",
                     _json.dumps(c["bboxes"], ensure_ascii=False),
                     c["block_type"]),
                )
                new_ids.append(cur.lastrowid)
            conn.execute(
                "UPDATE documents SET status='indexed', n_pages=?, n_chunks=?, error=NULL, "
                "indexed_at=datetime('now'), title=?, year=?, venue=?, category=? WHERE id=?",
                (payload["n_pages"], len(chunks), title,
                 payload["year"], venue or None, category or None, doc_id),
            )
        index.append(new_ids, vectors)
        index.save()
        return True
    except Exception as e:
        with db() as conn:
            conn.execute("UPDATE documents SET status='failed', error=? WHERE id=?",
                         (f"{type(e).__name__}: {e}"[:500], doc_id))
        return False


def start_ingest(paths: list[str], incremental: bool = True) -> dict:
    """后台批量摄取。incremental=True 时跳过已 indexed 文档。"""
    global _ingest_thread
    with _scan_lock:
        if _ingest_thread and _ingest_thread.is_alive():
            return {"started": False, "reason": "已有摄取任务在运行"}
        if _reembed_thread and _reembed_thread.is_alive():
            return {"started": False, "reason": "重嵌入进行中，请稍后再试"}
        _stop_event.clear()
        doc_ids: list[str] = []
        failed_register = []
        for p in paths:
            try:
                doc_id, created = register_or_get(p)
                doc = get_doc(doc_id)
                if incremental and doc and doc["status"] == "indexed" and not created:
                    continue
                doc_ids.append(doc_id)
            except Exception as e:
                failed_register.append({"path": p, "error": str(e)})

        _set_job(state="running", total=len(doc_ids), done=0, failed=0,
                 message="", started_at=time.strftime("%Y-%m-%d %H:%M:%S"))
        _ingest_thread = threading.Thread(
            target=_ingest_worker, args=(doc_ids,), daemon=True, name="cortex-ingest")
        _ingest_thread.start()
        return {"started": True, "queued": len(doc_ids),
                "skipped_registered_failures": failed_register}


def _ingest_worker(doc_ids: list[str]):
    done = failed = 0
    for i, doc_id in enumerate(doc_ids):
        if _stop_event.is_set():
            _set_job(state="error", message="已取消")
            return
        doc = get_doc(doc_id)
        _set_job(current_file=doc["filename"] if doc else doc_id)
        ok = process_document(doc_id)
        if ok:
            done += 1
        else:
            failed += 1
        _set_job(done=done, failed=failed)
    _set_job(state="done", current_file="", message=f"完成：成功 {done}，失败 {failed}")


def cancel_ingest():
    _stop_event.set()


def reembed_all() -> dict:
    """全量重嵌入：上下文前缀变更/换嵌入模型后重建向量。服务进程内后台执行。"""
    global _reembed_thread
    with _scan_lock:
        if _reembed_thread and _reembed_thread.is_alive():
            return {"started": False, "reason": "重嵌入已在运行"}
        if _ingest_thread and _ingest_thread.is_alive():
            return {"started": False, "reason": "摄取任务进行中，请稍后再试"}

        def worker():
            try:
                with db() as conn:
                    docs = conn.execute(
                        "SELECT id, title, venue, year FROM documents WHERE status='indexed'"
                    ).fetchall()
                total = len(docs)
                _set_job(state="running", total=total, done=0, failed=0,
                         message="全量重嵌入", current_file="")
                index = get_index()
                for i, d in enumerate(docs, 1):
                    if _stop_event.is_set():
                        _set_job(state="error", message="重嵌入已取消")
                        return
                    _set_job(current_file=f"重嵌入 {i}/{total}: {d['title'][:40]}")
                    with db() as conn:
                        rows = conn.execute(
                            "SELECT id, text FROM chunks WHERE doc_id=? ORDER BY seq", (d["id"],)
                        ).fetchall()
                    if not rows:
                        continue
                    ctx = f"《{d['title']}》"
                    if d["venue"]:
                        ctx += f" {d['venue']}"
                    if d["year"]:
                        ctx += f" {d['year']}"
                    ids = [r["id"] for r in rows]
                    texts = [f"{ctx} | {r['text']}" for r in rows]
                    vectors = embed_passages(texts)
                    index.remove_ids(set(ids))
                    index.append(ids, vectors)
                    _set_job(done=i)
                index.save()
                _set_job(state="done", current_file="", message=f"重嵌入完成：{total} 篇")
            except Exception as e:
                _set_job(state="error", message=f"重嵌入失败：{type(e).__name__}: {e}"[:300])

        _reembed_thread = threading.Thread(target=worker, daemon=True, name="cortex-reembed")
        _reembed_thread.start()
        return {"started": True}
