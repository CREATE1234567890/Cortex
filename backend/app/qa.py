"""RAG 问答管线（FR-QA-01~04 + FR-CT-02）。

PaperQA2 式解耦：检索出的证据先行组装并编号 [1..n]，
生成阶段被强制只引用这些编号；生成后做引用验证 pass：
- [n] 不在证据集内 → 从文本中剔除该引用（防幻觉引用）；
- 完全没有证据 → 拒答模板（US-04）；
- 未配置 LLM → 降级为"证据列举模式"，系统仍可用、可测。

SSE 事件流：
  evidence  → 证据列表（含引用跳转所需全部元数据）
  delta     → 增量文本
  citations → 验证后的引用元数据
  done      → 结束（含 message_id）
  error     → 错误
"""
from __future__ import annotations

import json
import re
import uuid
from collections.abc import Generator

from . import config
from .db import db, get_setting
from .llm import chat, llm_ready
from .retriever import Evidence, retrieve

CIT_RE = re.compile(r"\[(\d{1,2})\]")

SYSTEM_PROMPT = """你是 Cortex，一个严谨的个人科研文献库问答助手。你的回答必须遵守以下规则：

1. **只依据证据回答**：仅使用 <context> 中编号 [1]–[{max_n}] 的证据块回答问题，不使用任何库外知识。
2. **句级引用**：每个事实性论断的句尾必须紧跟引用编号，如"……提升分割精度 [2]。"。多个来源可并列引用，如 [1][3]。
3. **诚实拒答**：若证据不足以回答，直接说明"库中未找到足够的相关证据"，并简述已检索到的最接近内容是什么。绝不允许编造或猜测。
4. **忠实原文**：引用证据时保留原意，不夸大、不脑补数字。证据中的表格数据可引用并注明来源编号。
5. **语言**：用用户提问的语言回答；术语首次出现可附英文原文。
6. 格式：用 Markdown；适当分点；不要输出 <context> 标签本身。"""

EVIDENCE_BLOCK = """<context>
{blocks}
</context>"""

REFUSAL_TEMPLATE = """**库中未找到足够的相关证据，无法可靠回答该问题。**

本次检索使用的关键词：`{query}`，在 {n_docs} 篇文献的索引中最接近的内容如下：

{nearest}

如需回答此问题，建议补充相关文献入库，或换个问法（更具体的方法名/数据集名/指标名）。"""


def _format_evidence_blocks(evidences: list[Evidence]) -> str:
    parts = []
    for ev in evidences:
        sec = " > ".join(ev.section_path) if ev.section_path else ""
        head = f"[{ev.n}] 《{ev.title}》 p.{ev.page_start}" + (f"-{ev.page_end}" if ev.page_end != ev.page_start else "")
        if sec:
            head += f" ｜ {sec}"
        parts.append(f"{head}\n{ev.text}")
    return "\n\n".join(parts)


def _validate_citations(text: str, evidences: list[Evidence]) -> tuple[str, list[dict]]:
    """引用验证 pass：剔除不存在/越界的 [n]；返回(清理后文本, 引用元数据)。"""
    valid_ns = {ev.n for ev in evidences}
    used: list[int] = []

    def repl(m: re.Match) -> str:
        n = int(m.group(1))
        if n in valid_ns:
            if n not in used:
                used.append(n)
            return m.group(0)
        return ""  # 幻觉引用直接删除

    cleaned = CIT_RE.sub(repl, text)
    citations = []
    for n in used:
        ev = evidences[n - 1]
        citations.append({
            "n": n,
            "chunk_id": ev.chunk_id,
            "doc_id": ev.doc_id,
            "title": ev.title,
            "page": ev.page_start,
            "page_end": ev.page_end,
            "section_path": ev.section_path,
            "snippet": ev.snippet,
            "bboxes": ev.bboxes,
            "block_type": ev.block_type,
        })
    return cleaned, citations


def _history_messages(conversation_id: str, question: str) -> list[dict]:
    """多轮：取最近 N 轮历史（含本轮提问）。"""
    turns = get_setting("history_turns", str(config.HISTORY_TURNS))
    try:
        n = max(0, int(turns))
    except ValueError:
        n = config.HISTORY_TURNS
    msgs: list[dict] = []
    with db() as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? "
            "ORDER BY id DESC LIMIT ?", (conversation_id, n * 2),
        ).fetchall()
    for r in reversed(rows):
        msgs.append({"role": r["role"], "content": r["content"][:6000]})
    return msgs


def build_prompt(question: str, evidences: list[Evidence],
                 history: list[dict]) -> list[dict]:
    ctx = EVIDENCE_BLOCK.format(blocks=_format_evidence_blocks(evidences))
    msgs = [{"role": "system", "content": SYSTEM_PROMPT.replace("{max_n}", str(len(evidences)))}]
    for h in history:
        msgs.append(h)
    msgs.append({"role": "user", "content": f"{ctx}\n\n<question>{question}</question>"})
    return msgs


def evidence_only_answer(question: str, evidences: list[Evidence]) -> str:
    """LLM 未配置时的降级：直接列出证据块。"""
    if not evidences:
        return "知识库当前为空或未命中相关内容。请先在「文献库」页导入 PDF。"
    lines = [f"未配置 LLM（可在「设置」页填写 API Key），以下是与 **{question}** 最相关的库内证据：", ""]
    for ev in evidences:
        lines.append(f"**[{ev.n}] 《{ev.title}》 p.{ev.page_start}**")
        if ev.section_path:
            lines.append(f"*{(' > '.join(ev.section_path))}*")
        lines.append("")
        lines.append("> " + ev.text[:500].replace("\n", "\n> "))
        lines.append("")
    return "\n".join(lines)


def stream_answer(conversation_id: str, question: str,
                  doc_ids: list[str] | None = None) -> Generator[dict, None, None]:
    """完整问答流。yield 的事件 dict 由路由层转成 SSE。"""
    evidences = retrieve(question, doc_ids=doc_ids)

    yield {"event": "evidence", "data": {
        "evidences": [{
            "n": ev.n, "chunk_id": ev.chunk_id, "doc_id": ev.doc_id,
            "title": ev.title, "page": ev.page_start, "page_end": ev.page_end,
            "section_path": ev.section_path, "snippet": ev.snippet,
            "bboxes": ev.bboxes, "score": round(ev.score, 4),
        } for ev in evidences],
        "llm_ready": llm_ready(),
    }}

    # 落库用户消息
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content) VALUES (?, 'user', ?)",
            (conversation_id, question),
        )

    if not evidences:
        nearest = retrieve(question, top_k=3)  # 拒答时给出最接近内容
        near_txt = "\n".join(
            f"- 《{ev.title}》 p.{ev.page_start}：{ev.snippet}…" for ev in nearest
        ) or "（无）"
        with db() as conn:
            n_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE status='indexed'").fetchone()["c"]
        answer = REFUSAL_TEMPLATE.format(query=question, n_docs=n_docs, nearest=near_txt)
        yield {"event": "delta", "data": {"text": answer}}
        _save_assistant(conversation_id, answer, [])
        yield {"event": "citations", "data": {"citations": []}}
        yield {"event": "done", "data": {"refused": True}}
        return

    if not llm_ready():
        answer = evidence_only_answer(question, evidences)
        yield {"event": "delta", "data": {"text": answer}}
        _, citations = _validate_citations(answer, evidences)
        _save_assistant(conversation_id, answer, citations)
        yield {"event": "citations", "data": {"citations": citations}}
        yield {"event": "done", "data": {"refused": False}}
        return

    history = _history_messages(conversation_id, question)
    prompt = build_prompt(question, evidences, history)

    buf = ""
    try:
        stream = chat(prompt, stream=True)
        for chunk in stream:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                buf += delta
                yield {"event": "delta", "data": {"text": delta}}
    except Exception as e:  # 网络/Key 错误 → 降级证据模式
        err = f"\n\n> ⚠️ LLM 调用失败（{type(e).__name__}），已降级为证据列举模式。请检查「设置」中的 API 配置。"
        yield {"event": "delta", "data": {"text": err}}
        if not buf:
            buf = evidence_only_answer(question, evidences)
            yield {"event": "delta", "data": {"text": buf}}

    cleaned, citations = _validate_citations(buf, evidences)
    _save_assistant(conversation_id, cleaned, citations)
    yield {"event": "citations", "data": {"citations": citations}}
    yield {"event": "done", "data": {"refused": False}}


def _save_assistant(conversation_id: str, content: str, citations: list[dict]):
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, citations) VALUES (?, 'assistant', ?, ?)",
            (conversation_id, content, json.dumps(citations, ensure_ascii=False)),
        )


def new_conversation(title: str = "新对话") -> str:
    cid = f"conv_{uuid.uuid4().hex[:12]}"
    with db() as conn:
        conn.execute("INSERT INTO conversations(id, title) VALUES (?, ?)", (cid, title))
    return cid


def maybe_rename_conversation(conversation_id: str, first_question: str):
    with db() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM messages WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        if row["c"] <= 1:
            conn.execute("UPDATE conversations SET title=? WHERE id=?",
                         (first_question[:40], conversation_id))
