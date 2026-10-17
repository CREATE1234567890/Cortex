"""RAG 问答管线（FR-QA-01~04 + FR-CT-02 + Agentic 检索循环 + 长期记忆）。

三段式（Hermes 式"推理→工具→记忆"在科研问答上的落地）：
1. 记忆召回：FTS5 召回长期记忆，压缩后注入 system（明确标注不可作为文献证据引用）；
2. Agentic 检索循环：初始检索后，模型可通过 search_kb 工具自行改写查询补充检索，
   最多 3 轮（预算封顶）；每轮流式输出，工具调用期间的内容作为状态展示；
3. 引用验证：最终文本中不在证据集内的 [n] 直接剔除（防幻觉引用）；
   无 LLM 时降级为证据列举模式，无证据且模型未检索到时诚实拒答。

SSE 事件流：
  evidence  → 初始证据列表
  tool      → 补充检索状态 {query, phase: start/done, added, evidences(新增)}
  delta     → 增量文本
  citations → 验证后的引用元数据
  done      → 结束
  error     → 错误
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from collections.abc import Generator

from . import config
from .db import db, get_setting
from .llm import chat, llm_ready
from .retriever import Evidence, retrieve

CIT_RE = re.compile(r"\[(\d{1,2})\]")

MAX_TOOL_ROUNDS = 3

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_kb",
        "description": (
            "在个人文献库中补充检索证据。当已有证据不足以回答时调用："
            "换一个更精确的关键词（论文术语、方法名、数据集名、指标名），或从不同角度检索。"
            "不要重复已经检索过的词。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询词，中英文均可"},
                "k": {"type": "integer", "description": "返回证据块数量，默认 5",
                      "enum": [3, 5, 8]},
            },
            "required": ["query"],
        },
    },
}]

# KV-cache 友好设计：system + 工具定义逐字节稳定（无任何插值），
# 动态内容（记忆/证据块/编号上限）全部放在对话尾部的 user 消息里，
# 使服务商前缀缓存可以跨轮、跨提问复用。
SYSTEM_PROMPT = """你是 Cortex，一个严谨的个人科研文献库问答助手。你的回答必须遵守以下规则：

1. **只依据证据回答**：仅使用本对话中给出的编号证据块（如 [2]、[7]）回答问题，不使用任何库外知识。
2. **句级引用**：每个事实性论断的句尾必须紧跟引用编号，如"……提升分割精度 [2]。"。多个来源可并列引用，如 [1][3]。不得引用未出现过的编号。
3. **诚实拒答**：若证据不足以回答，直接说明"库中未找到足够的相关证据"，并简述已检索到的最接近内容是什么。绝不允许编造或猜测。
4. **忠实原文**：引用证据时保留原意，不夸大、不脑补数字。证据中的表格数据可引用并注明来源编号。
5. **语言**：用用户提问的语言回答；术语首次出现可附英文原文。
6. 格式：用 Markdown；适当分点；不要输出 <context> 标签本身。
7. **补充检索工具**：你可以调用 search_kb 工具在库中检索更多证据。仅在现有证据明显不足时使用；每次换更精确的查询词；最多调用 3 次。最终回答仍只允许引用上文编号体系中的证据。"""

MEMORY_BLOCK = """<memory>
以下是关于用户的长期记忆（仅供个性化参考，不是文献证据，禁止为其中内容添加引用编号）：
{memories}
</memory>"""

EVIDENCE_BLOCK = """<context>
{blocks}
</context>"""

REFUSAL_ZH = """**库中未找到足够的相关证据，无法可靠回答该问题。**

本次检索使用的关键词：`{query}`，在 {n_docs} 篇文献的索引中最接近的内容如下：

{nearest}

如需回答此问题，建议补充相关文献入库，或换个问法（更具体的方法名/数据集名/指标名）。"""

REFUSAL_EN = """**Not enough relevant evidence in your library to answer this reliably.**

Query used: `{query}`. The closest matches among {n_docs} indexed papers:

{nearest}

Consider importing more related papers, or rephrase with more specific method/dataset/metric names."""


def _refusal(query: str, n_docs: int, nearest: str, question: str) -> str:
    tpl = REFUSAL_ZH if re.search(r"[\u4e00-\u9fff]", question) else REFUSAL_EN
    return tpl.format(query=query, n_docs=n_docs, nearest=nearest)


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


def _history_messages(conversation_id: str) -> list[dict]:
    """多轮：滚动摘要（如有）+ 最近 N 轮原文。摘要消息放在最前，之后保持追加式稳定。"""
    turns = get_setting("history_turns", str(config.HISTORY_TURNS))
    try:
        n = max(0, int(turns))
    except ValueError:
        n = config.HISTORY_TURNS
    msgs: list[dict] = []
    with db() as conn:
        row = conn.execute("SELECT summary FROM conversations WHERE id=?",
                           (conversation_id,)).fetchone()
        summary = row["summary"] if row else None
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE conversation_id=? "
            "ORDER BY id DESC LIMIT ?", (conversation_id, n * 2),
        ).fetchall()
    if summary:
        msgs.append({"role": "system",
                     "content": f"（此前对话的滚动摘要，供延续语境）\n{summary}"})
    for r in reversed(rows):
        msgs.append({"role": r["role"], "content": r["content"][:6000]})
    return msgs


def _user_message(question: str, evidences: list[Evidence], memory_text: str | None) -> str:
    """动态尾部：记忆 + 证据块 + 问题。system 与工具定义保持逐字节稳定。"""
    parts = []
    if memory_text:
        parts.append(MEMORY_BLOCK.replace("{memories}", memory_text))
    parts.append(EVIDENCE_BLOCK.format(blocks=_format_evidence_blocks(evidences) or "（初始检索无结果）"))
    parts.append(f"<question>{question}</question>")
    parts.append(f"（当前共有编号 [1]–[{len(evidences)}] 的证据块，补充检索可能增加编号。）")
    return "\n\n".join(parts)


def evidence_only_answer(question: str, evidences: list[Evidence]) -> str:
    """LLM 未配置时的降级：直接列出证据块。文案跟随提问语言。"""
    zh = bool(re.search(r"[\u4e00-\u9fff]", question))
    if not evidences:
        return ("知识库当前为空或未命中相关内容。请先在「文献库」页导入 PDF。"
                if zh else
                "The library is empty or nothing matched. Import PDFs on the Library page first.")
    head = (f"未配置 LLM（可在「设置」页填写 API Key），以下是与 **{question}** 最相关的库内证据："
            if zh else
            f"No LLM configured (add an API Key in Settings). Closest evidence in your library for **{question}**:")
    lines = [head, ""]
    for ev in evidences:
        lines.append(f"**[{ev.n}] 《{ev.title}》 p.{ev.page_start}**")
        if ev.section_path:
            lines.append(f"*{(' > '.join(ev.section_path))}*")
        lines.append("")
        lines.append("> " + ev.text[:500].replace("\n", "\n> "))
        lines.append("")
    return "\n".join(lines)


def _memory_for(question: str) -> str | None:
    """召回长期记忆（失败静默）。可在设置中关闭。"""
    if get_setting("memory_enabled", "on") != "on":
        return None
    try:
        from .memory import recall_condensed
        return recall_condensed(question)
    except Exception:
        return None


def _run_search_kb(args: dict, evidences: list[Evidence], seen: set[int]) -> tuple[list[Evidence], str]:
    """执行 search_kb 工具：追加新证据（全局重编号），返回(新增证据, 给模型的文本)。"""
    query = str(args.get("query", "")).strip()
    try:
        k = int(args.get("k", 5))
    except (TypeError, ValueError):
        k = 5
    k = min(max(k, 1), 8)
    if not query:
        return [], "（查询为空，未检索）"
    results = retrieve(query, top_k=k)
    new = [ev for ev in results if ev.chunk_id not in seen]
    for ev in new:
        seen.add(ev.chunk_id)
        evidences.append(ev)
    if not new:
        return [], (f"（查询「{query}」无新增证据，返回了 {len(results)} 个已有证据块。"
                    f"请换一个角度的关键词，或基于现有证据回答。）")
    lines = [f"补充检索「{query}」新增 {len(new)} 个证据块："]
    for ev in new:
        sec = " > ".join(ev.section_path) if ev.section_path else ""
        head = f"[{ev.n}] 《{ev.title}》 p.{ev.page_start}"
        if sec:
            head += f" ｜ {sec}"
        lines.append(f"{head}\n{ev.text[:2000]}")
    return new, "\n\n".join(lines)


def stream_answer(conversation_id: str, question: str,
                  doc_ids: list[str] | None = None) -> Generator[dict, None, None]:
    """完整问答流（含 Agentic 检索循环 + 记忆）。yield 的 dict 由路由层转成 SSE。"""
    evidences: list[Evidence] = retrieve(question, doc_ids=doc_ids)
    seen = {ev.chunk_id for ev in evidences}

    def ev_payload(list_: list[Evidence]) -> list[dict]:
        return [{
            "n": ev.n, "chunk_id": ev.chunk_id, "doc_id": ev.doc_id,
            "title": ev.title, "page": ev.page_start, "page_end": ev.page_end,
            "section_path": ev.section_path, "snippet": ev.snippet,
            "bboxes": ev.bboxes, "score": round(ev.score, 4),
        } for ev in list_]

    yield {"event": "evidence", "data": {
        "evidences": ev_payload(evidences),
        "llm_ready": llm_ready(),
    }}

    with db() as conn:
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content) VALUES (?, 'user', ?)",
            (conversation_id, question),
        )

    if not llm_ready():
        if not evidences:
            answer = evidence_only_answer(question, [])
            yield {"event": "delta", "data": {"text": answer}}
            _save_assistant(conversation_id, answer, [])
            yield {"event": "citations", "data": {"citations": []}}
            yield {"event": "done", "data": {"refused": True}}
            return
        answer = evidence_only_answer(question, evidences)
        yield {"event": "delta", "data": {"text": answer}}
        _, citations = _validate_citations(answer, evidences)
        _save_assistant(conversation_id, answer, citations)
        yield {"event": "citations", "data": {"citations": citations}}
        yield {"event": "done", "data": {"refused": False}}
        return

    # ---- Agentic 检索循环（流式，工具调用轮的内容作为状态，stop 轮为最终答案）----
    memory_text = _memory_for(question)
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},  # 逐字节稳定，命中前缀缓存
        *_history_messages(conversation_id),
        {"role": "user", "content": _user_message(question, evidences, memory_text)},
    ]

    final_text = ""
    for _ in range(MAX_TOOL_ROUNDS):
        buf = ""
        tool_calls: dict[int, dict] = {}
        finish_reason = "stop"
        try:
            stream = chat(messages, stream=True, tools=TOOLS)
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta and delta.content:
                    buf += delta.content
                    yield {"event": "delta", "data": {"text": delta.content}}
                if delta and getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function and tc.function.name:
                            slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            slot["arguments"] += tc.function.arguments
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except Exception as e:
            # 网络/Key/不支持 tools → 降级为证据模式
            err = f"\n\n> ⚠️ LLM 调用失败（{type(e).__name__}），已降级为证据列举模式。请检查「设置」中的 API 配置。"
            yield {"event": "delta", "data": {"text": err}}
            final_text = final_text or evidence_only_answer(question, evidences)
            yield {"event": "delta", "data": {"text": final_text}}
            break

        if finish_reason == "tool_calls" and tool_calls:
            # 工具轮：执行检索后进入下一轮（已流出的前置文本保留为过程说明）
            messages.append({"role": "assistant", "content": buf or None, "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": tc["arguments"] or "{}"}}
                for tc in tool_calls.values() if tc["name"]
            ]})
            for tc in tool_calls.values():
                if tc["name"] != "search_kb":
                    messages.append({"role": "tool", "tool_call_id": tc["id"] or "t0",
                                     "content": "（未知工具）"})
                    continue
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}
                query = str(args.get("query", ""))
                yield {"event": "tool", "data": {"phase": "start", "query": query}}
                new, result_text = _run_search_kb(args, evidences, seen)
                yield {"event": "tool", "data": {
                    "phase": "done", "query": query, "added": len(new),
                    "total": len(evidences), "evidences": ev_payload(new),
                }}
                messages.append({"role": "tool", "tool_call_id": tc["id"] or "t0",
                                 "content": result_text})
            continue

        # stop：最终答案
        final_text = buf
        break

    if not final_text:
        # 预算耗尽时模型仍在请求工具 → 强制做一次无工具的最终生成
        messages.append({"role": "user", "content":
            "（检索预算已用完）请立即基于现有证据回答上述问题；"
            "若证据确实不足，请明确说明库中未找到足够证据。"})
        buf = ""
        try:
            stream = chat(messages, stream=True)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    buf += delta.content
                    yield {"event": "delta", "data": {"text": delta.content}}
        except Exception:
            pass
        final_text = buf

    if not evidences:
        with db() as conn:
            n_docs = conn.execute(
                "SELECT COUNT(*) c FROM documents WHERE status='indexed'").fetchone()["c"]
        nearest = retrieve(question, top_k=3)
        near_txt = "\n".join(
            f"- 《{ev.title}》 p.{ev.page_start}：{ev.snippet}…" for ev in nearest) or "（无）"
        answer = _refusal(question, n_docs, near_txt, question)
        yield {"event": "delta", "data": {"text": answer}}
        _save_assistant(conversation_id, answer, [])
        yield {"event": "citations", "data": {"citations": []}}
        yield {"event": "done", "data": {"refused": True}}
        return

    cleaned, citations = _validate_citations(final_text, evidences)
    _save_assistant(conversation_id, cleaned, citations)
    yield {"event": "citations", "data": {"citations": citations}}
    yield {"event": "done", "data": {"refused": False}}

    # 会话后沉淀记忆（后台线程，不阻塞）
    if cleaned and not cleaned.startswith("**库中未找到"):
        from .memory import extract_async
        extract_async(conversation_id, question, cleaned)
        maybe_summarize_async(conversation_id)


def _save_assistant(conversation_id: str, content: str, citations: list[dict]):
    with db() as conn:
        conn.execute(
            "INSERT INTO messages(conversation_id, role, content, citations) VALUES (?, 'assistant', ?, ?)",
            (conversation_id, content, json.dumps(citations, ensure_ascii=False)),
        )


def maybe_summarize_async(conversation_id: str):
    """滚动摘要（上下文工程 Compaction）：超出窗口的旧轮次用 LLM 压实成要点，
    与已有摘要合并；最近 HISTORY_TURNS*2 条始终保留原文。后台线程，失败静默。"""

    def _run():
        try:
            from .llm import chat_text
            keep = config.HISTORY_TURNS * 2
            with db() as conn:
                row = conn.execute(
                    "SELECT summary, summarized_until, "
                    "(SELECT MAX(id) FROM messages WHERE conversation_id=c.id) max_id "
                    "FROM conversations c WHERE c.id=?", (conversation_id,)).fetchone()
                if not row:
                    return
                until = row["summarized_until"] or 0
                overflow = conn.execute(
                    "SELECT role, content FROM messages WHERE conversation_id=? "
                    "AND id > ? AND id <= ? - ? ORDER BY id",
                    (conversation_id, until, row["max_id"] or 0, keep)).fetchall()
            if len(overflow) < 2:
                return
            transcript = "\n".join(f"{'用户' if r['role'] == 'user' else '助手'}: {r['content'][:800]}"
                                   for r in overflow)[:6000]
            prev = row["summary"] or "（无）"
            out = chat_text([{"role": "user", "content":
                f"把以下对话片段压缩为要点摘要（150 字以内），保留：研究主题、提到的论文/方法/数据集、"
                f"未解决的问题、用户偏好。与已有摘要合并去重，直接输出摘要正文。\n\n"
                f"【已有摘要】\n{prev}\n\n【新对话片段】\n{transcript}"}], temperature=0)
            out = out.strip()[:800]
            if not out:
                return
            max_id = row["max_id"] or 0
            new_until = (max_id - keep) if max_id > keep else 0
            with db() as conn:
                conn.execute("UPDATE conversations SET summary=?, summarized_until=? WHERE id=?",
                             (out, new_until, conversation_id))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="cortex-summary").start()


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
