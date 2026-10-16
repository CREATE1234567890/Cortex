"""检索层：稠密 + BM25 → RRF 融合 → 证据块组装（FR-IR-01/02）。

返回的证据块带全局编号 [1..n]，是引用溯源的运行时形态。
"""
from __future__ import annotations

from dataclasses import dataclass

from . import config
from .db import db, loads
from .embedding import embed_query
from .indexer import get_index, fts_search, rrf_fuse


@dataclass
class Evidence:
    n: int                 # 全局编号，从 1 开始
    chunk_id: int
    doc_id: str
    title: str
    page_start: int        # 1-based 展示
    page_end: int
    section_path: list[str]
    text: str
    bboxes: list[dict]
    block_type: str
    score: float
    snippet: str           # 短摘录（引用卡片展示用）


def _doc_filter(doc_ids: list[str] | None) -> set[int] | None:
    if not doc_ids:
        return None
    with db() as conn:
        q = ",".join("?" * len(doc_ids))
        rows = conn.execute(f"SELECT id FROM chunks WHERE doc_id IN ({q})", doc_ids).fetchall()
    return {r["id"] for r in rows}


def retrieve(query: str, top_k: int = config.FINAL_TOPK,
             doc_ids: list[str] | None = None) -> list[Evidence]:
    allowed = _doc_filter(doc_ids)

    dense_hits: list[tuple[int, float]] = []
    try:
        qvec = embed_query(query)
        dense_hits = get_index().search(qvec, config.DENSE_TOPK, allowed)
    except Exception:
        pass  # 模型未就绪时退化为纯 BM25
    bm25_hits = fts_search(query, config.BM25_TOPK, allowed)

    fused = rrf_fuse(dense_hits, bm25_hits)[:top_k]
    if not fused:
        return []

    id2score = dict(fused)
    with db() as conn:
        q = ",".join("?" * len(id2score))
        rows = conn.execute(
            f"""SELECT c.id, c.doc_id, c.text, c.section_path, c.page_start, c.page_end,
                       c.bbox, c.block_type, d.title
                FROM chunks c JOIN documents d ON d.id = c.doc_id
                WHERE c.id IN ({q})""",
            list(id2score.keys()),
        ).fetchall()

    rows.sort(key=lambda r: -id2score[r["id"]])
    evidences = []
    for i, r in enumerate(rows, start=1):
        bboxes = loads(r["bbox"], [])
        evidences.append(Evidence(
            n=i,
            chunk_id=r["id"],
            doc_id=r["doc_id"],
            title=r["title"],
            page_start=r["page_start"] + 1,
            page_end=r["page_end"] + 1,
            section_path=loads(r["section_path"], []),
            text=r["text"],
            bboxes=bboxes,
            block_type=r["block_type"],
            score=id2score[r["id"]],
            snippet=r["text"][:220].replace("\n", " "),
        ))
    return evidences
