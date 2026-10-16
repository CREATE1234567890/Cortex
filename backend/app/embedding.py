"""嵌入层：sentence-transformers 本地模型（e5 系列需 query/passage 前缀）。

模型加载是单例（懒加载），进程内复用。
"""
from __future__ import annotations

import re
import threading

from . import config

_model = None
_model_name: str | None = None
_lock = threading.Lock()

_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "

# PDF 提取文本可能混入控制字符（\x00 等），tokenizer 会直接抛异常
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize(text: str) -> str:
    return _CTRL_RE.sub(" ", text)


def _load():
    global _model, _model_name
    with _lock:
        if _model is not None and _model_name == config.EMBED_MODEL:
            return _model
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(config.EMBED_MODEL, device="cpu")
        _model_name = config.EMBED_MODEL
        return _model


def embed_passages(texts: list[str]) -> list[list[float]]:
    model = _load()
    return model.encode(
        [_PASSAGE_PREFIX + _sanitize(t) for t in texts],
        batch_size=config.EMBED_BATCH,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).tolist()


def embed_query(text: str) -> list[float]:
    model = _load()
    return model.encode(
        [_QUERY_PREFIX + _sanitize(text)],
        normalize_embeddings=True,
        show_progress_bar=False,
    )[0].tolist()
