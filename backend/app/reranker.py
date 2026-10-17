"""Rerank 精排层（FR-IR-03）：CrossEncoder 对 (query, chunk) 打分重排。

单例懒加载；模型缺失或加载失败时 rerank_ready()=False，检索层自动跳过。
"""
from __future__ import annotations

import threading

from . import config

_model = None
_lock = threading.Lock()
_failed = False


def rerank_ready() -> bool:
    if get_setting_off():
        return False
    if _failed:
        return False
    try:
        _load()
        return True
    except Exception:
        return False


def get_setting_off() -> bool:
    from .db import get_setting
    return get_setting("rerank_enabled", "on") != "on"


def _load():
    global _model
    with _lock:
        if _model is not None:
            return _model
        from sentence_transformers import CrossEncoder
        _model = CrossEncoder(config.RERANK_MODEL, max_length=config.RERANK_MAX_LEN,
                              device="cpu")
        return _model


def rerank(query: str, candidates: list[tuple[int, str]]) -> list[tuple[int, float]]:
    """candidates: [(chunk_id, text)]。返回按相关性降序的 [(chunk_id, score)]。"""
    model = _load()
    pairs = [(query, text) for _, text in candidates]
    scores = model.predict(pairs, batch_size=16, show_progress_bar=False)
    scored = sorted(zip((cid for cid, _ in candidates), (float(s) for s in scores)),
                    key=lambda x: -x[1])
    return scored
