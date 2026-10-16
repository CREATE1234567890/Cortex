"""索引层：numpy 向量库 + FTS5 BM25。

向量：归一化矩阵驻留内存，.npy 落盘；row i 对应 chunk_db_id[i]。
FTS5：rowid = chunks.id，删除文档时同步清理。
Qdrant 等可在此接口后替换（接口：index/search/delete_doc）。
"""
from __future__ import annotations

import numpy as np

from . import config
from .db import db, loads


class VectorIndex:
    def __init__(self):
        self.matrix: np.ndarray | None = None   # (n, dim) float32, 归一化
        self.chunk_ids: list[int] = []
        self._load()

    def _npy_path(self):
        return config.VECTOR_DIR / f"emb_{config.EMBED_DIM}.npy"
        # 模型更换时维度不同即自动换文件；同名模型更换需手动删除

    def _meta_path(self):
        return config.VECTOR_DIR / f"emb_{config.EMBED_DIM}_ids.json"

    def _load(self):
        import json
        if self._npy_path().exists() and self._meta_path().exists():
            self.matrix = np.load(self._npy_path())
            self.chunk_ids = json.loads(self._meta_path().read_text(encoding="utf-8"))
        else:
            self.matrix = np.zeros((0, config.EMBED_DIM), dtype=np.float32)
            self.chunk_ids = []

    def save(self):
        import json
        np.save(self._npy_path(), self.matrix)
        self._meta_path().write_text(
            json.dumps(self.chunk_ids), encoding="utf-8")

    def append(self, chunk_ids: list[int], vectors: list[list[float]]):
        mat = np.asarray(vectors, dtype=np.float32)
        if mat.ndim != 2 or mat.shape[0] != len(chunk_ids):
            raise ValueError(f"向量形状异常: {mat.shape} vs {len(chunk_ids)} ids")
        if self.matrix is None or self.matrix.shape[0] == 0:
            self.matrix = mat
        else:
            self.matrix = np.vstack([self.matrix, mat])
        self.chunk_ids.extend(chunk_ids)

    def search(self, query_vec: list[float], top_k: int,
               allowed_ids: set[int] | None = None) -> list[tuple[int, float]]:
        if self.matrix is None or self.matrix.shape[0] == 0:
            return []
        q = np.asarray(query_vec, dtype=np.float32)
        scores = self.matrix @ q
        order = np.argsort(-scores)
        out: list[tuple[int, float]] = []
        for idx in order:
            cid = self.chunk_ids[int(idx)]
            if allowed_ids is not None and cid not in allowed_ids:
                continue
            out.append((cid, float(scores[int(idx)])))
            if len(out) >= top_k:
                break
        return out

    def remove_ids(self, ids: set[int]):
        """按 chunk id 精确移除向量行（重摄取/删除文档时保持索引一致）。"""
        if not ids or self.matrix is None or self.matrix.shape[0] == 0:
            return
        keep_mask = [cid not in ids for cid in self.chunk_ids]
        self.chunk_ids = [c for c, k in zip(self.chunk_ids, keep_mask) if k]
        mask = np.asarray(keep_mask)
        self.matrix = self.matrix[mask]

    def delete_doc(self, doc_id: str):
        """删除某文档全部向量（按 DB 现存 chunk id；调用时 chunks 行须仍存在）。"""
        with db() as conn:
            rows = conn.execute("SELECT id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()
        self.remove_ids({r["id"] for r in rows})
        self.save()


_index: VectorIndex | None = None


def get_index() -> VectorIndex:
    global _index
    if _index is None:
        _index = VectorIndex()
    return _index


# ---------- FTS5（external-content 模式：写入/删除由 chunks 表触发器自动同步） ----------


def fts_search(query: str, top_k: int,
               allowed_ids: set[int] | None = None) -> list[tuple[int, float]]:
    """BM25 检索。查询词做最小清洗，OR 连接保证宽松匹配。"""
    import re
    terms = re.findall(r"[\w\u4e00-\u9fff]+", query)
    terms = [t for t in terms if t][:12]
    if not terms:
        return []
    q = " OR ".join(f'"{t}"' for t in terms)
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT rowid, bm25(chunks_fts) AS score FROM chunks_fts "
                "WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
                (q, top_k * 3),
            ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        cid = r["rowid"]
        if allowed_ids is not None and cid not in allowed_ids:
            continue
        out.append((cid, -float(r["score"])))  # bm25 越小越好 → 取负
        if len(out) >= top_k:
            break
    return out


def rrf_fuse(*ranked_lists: list[tuple[int, float]], k: int = config.RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion：多路名次融合。"""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for rank, (cid, _s) in enumerate(ranked):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda x: -x[1])
