"""长期记忆（参考 Hermes Agent：FTS5 跨会话召回 + LLM 摘要 + 会话后沉淀）。

原则：记忆是增强不是依赖——所有路径失败都静默降级，绝不阻塞问答主流程。
"""
from __future__ import annotations

import json
import re
import threading

from .db import db

_EXTRACT_PROMPT = """从下面这轮问答中提取值得长期记住的信息，用于个性化后续对话。只提取这几类：
- research_context：用户的研究方向/在做的课题/关注的论文与方法
- preference：交互偏好（语言、详略、回答风格）
- fact：用户明确告知的事实（如"我用的数据集是 BraTS"）

规则：
- 只提取确定、具体、有复用价值的信息；客套话、一次性任务、文献内容本身都不要
- 每条一句话，40 字以内，用第三人称（"用户在研究…"）
- 没有值得记的就返回 []

只输出 JSON 数组，形如：[{{"kind": "research_context", "content": "…"}}]"""

_FTS_TERM_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}")


# ---------- 写入（会话结束后台调用） ----------

def extract_and_store(conversation_id: str, user_msg: str, assistant_msg: str):
    """用 LLM 从一轮问答中提取记忆并入库。在后台线程调用，异常静默。"""
    try:
        from .llm import chat_text
        text = chat_text(
            [{"role": "user", "content":
              _EXTRACT_PROMPT + f"\n\n<user>\n{user_msg[:2500]}\n</user>\n<assistant>\n{assistant_msg[:2500]}\n</assistant>"}],
            temperature=0,
        )
        text = text.strip()
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return 0
        items = json.loads(m.group(0))
        added = 0
        for it in items[:5]:
            if not isinstance(it, dict):
                continue
            content = str(it.get("content", "")).strip()
            kind = str(it.get("kind", "research_context")).strip()
            if not content or len(content) > 200:
                continue
            if _similar_exists(content):
                continue
            with db() as conn:
                conn.execute(
                    "INSERT INTO memories(kind, content, source_conv) VALUES (?,?,?)",
                    (kind if kind in ("research_context", "preference", "fact") else "research_context",
                     content, conversation_id),
                )
            added += 1
        return added
    except Exception:
        return 0


def _similar_exists(content: str) -> bool:
    """近重检查：与现有记忆 FTS 匹配到高度重叠的词条则视为重复。"""
    terms = [t for t in _FTS_TERM_RE.findall(content) if len(t) >= 2][:8]
    if not terms:
        return False
    try:
        with db() as conn:
            row = conn.execute(
                "SELECT content FROM memories_fts JOIN memories ON memories.id = memories_fts.rowid "
                "WHERE memories_fts MATCH ? LIMIT 1",
                " OR ".join(f'"{t}"' for t in terms),
            ).fetchone()
    except Exception:
        return False
    if not row:
        return False
    old_terms = set(_FTS_TERM_RE.findall(row["content"]))
    new_terms = set(_FTS_TERM_RE.findall(content))
    if not old_terms:
        return False
    overlap = len(old_terms & new_terms) / max(1, len(old_terms | new_terms))
    return overlap >= 0.6


# ---------- 读取（问答前注入） ----------

def recall(query: str, top_k: int = 6) -> list[dict]:
    """FTS5 召回相关记忆；无命中时回退最近记忆（数量减半）。失败返回空。"""
    try:
        with db() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
            if total == 0:
                return []
            terms = [t for t in _FTS_TERM_RE.findall(query) if len(t) >= 2][:10]
            if terms:
                try:
                    rows = conn.execute(
                        "SELECT m.id, m.kind, m.content FROM memories_fts f "
                        "JOIN memories m ON m.id = f.rowid WHERE memories_fts MATCH ? "
                        "ORDER BY m.updated_at DESC LIMIT ?",
                        (" OR ".join(f'"{t}"' for t in terms), top_k),
                    ).fetchall()
                    if rows:
                        return [dict(r) for r in rows]
                except Exception:
                    pass
            rows = conn.execute(
                "SELECT id, kind, content FROM memories ORDER BY updated_at DESC LIMIT ?",
                (max(2, top_k // 2),),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def recall_condensed(query: str) -> str | None:
    """召回 + 必要时 LLM 压缩为几句话（Hermes 式摘要注入）。失败返回 None。"""
    items = recall(query)
    if not items:
        return None
    joined = "\n".join(f"- {it['content']}" for it in items)
    if len(items) <= 4 and len(joined) <= 600:
        return joined  # 少而短，直接用，省一次 LLM 调用
    try:
        from .llm import chat_text
        text = chat_text(
            [{"role": "user", "content":
              "把以下关于用户的长期记忆压缩为不超过 5 条、每条一句话的要点，"
              "与当前提问无关的条目丢弃。直接输出要点列表。\n\n"
              f"当前提问：{query[:500]}\n\n记忆：\n{joined}"}],
            temperature=0,
        )
        text = text.strip()
        return text[:1200] if text else joined[:800]
    except Exception:
        return joined[:800]


# ---------- 管理（设置页） ----------

def list_memories() -> list[dict]:
    with db() as conn:
        rows = conn.execute(
            "SELECT id, kind, content, created_at FROM memories ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]


def delete_memory(mem_id: int):
    with db() as conn:
        conn.execute("DELETE FROM memories WHERE id=?", (mem_id,))


def clear_memories():
    with db() as conn:
        conn.execute("DELETE FROM memories")


def extract_async(conversation_id: str, user_msg: str, assistant_msg: str):
    """后台线程沉淀记忆，不阻塞响应。"""
    def _run():
        extract_and_store(conversation_id, user_msg, assistant_msg)
    threading.Thread(target=_run, daemon=True, name="cortex-memory").start()
