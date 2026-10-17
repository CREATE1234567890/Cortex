# -*- coding: utf-8 -*-
"""引用正确率自动评估（FR-SM-04 完整版，项目门禁指标 ≥95%）。

流程：golden set 抽样 → 走真实 /api/chat SSE 全链路生成带引用回答（含 Agentic 检索）
→ 解析每个 [n] 引用的论断句 → LLM 逐条判定"该证据是否支持该论断" → 引用正确率。

用法：python scripts/eval_answer.py [抽样数，默认 12]（后端须在 8300 运行）
结果追加到 backend/data/eval_history.json（type: answer）
"""
import json
import os
import re
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

API = "http://127.0.0.1:8300"
CIT_RE = re.compile(r"\[(\d{1,2})\]")
SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")

JUDGE_PROMPT = """你是科研问答的引用审核员。判断下面的证据片段是否支持该论断。

论断：{claim}

证据片段（来自论文《{title}》第 {page} 页）：
{snippet}

判定标准：证据中包含论断所述信息的直接依据即算支持（允许同义转述；论断只是证据内容的子集或概括都算支持）。
只输出 JSON：{{"supported": true/false}}"""


def judge(claim: str, title: str, page: int, chunk_id: int, debug: list | None = None) -> bool | None:
    """直接 LLM 判定（用完整块文本而非摘要；不走 RAG 链路）。失败返回 None。"""
    try:
        from app.llm import chat_text
        from app.db import db
        with db() as conn:
            row = conn.execute("SELECT text FROM chunks WHERE id=?", (chunk_id,)).fetchone()
        full_text = (row["text"] if row else "")[:1800]
        out = chat_text([{"role": "user", "content": JUDGE_PROMPT.format(
            claim=claim[:600], title=title[:80], page=page, snippet=full_text)}],
            temperature=0)
        if '"supported": true' in out or '"supported":true' in out:
            return True
        if '"supported": false' in out or '"supported":false' in out:
            if debug is not None and len(debug) < 6:
                debug.append({"claim": claim[:200], "chunk": full_text[:300]})
            return False
        return None
    except Exception:
        return None


def sse_answer(question: str) -> tuple[str, list[dict], bool]:
    """走真实 SSE 全链路，返回 (回答文本, 引用元数据列表, 是否拒答)。"""
    text, cites, refused = "", [], False
    with httpx.stream("POST", f"{API}/api/chat",
                      json={"question": question}, timeout=600) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                event, data = None, None
                for line in block.split("\n"):
                    if line.startswith("event: "):
                        event = line[7:].strip()
                    elif line.startswith("data: "):
                        data = line[6:]
                if not data:
                    continue
                try:
                    d = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if event == "delta":
                    text += d.get("text", "")
                elif event == "citations":
                    cites = d.get("citations", [])
                elif event == "done":
                    refused = d.get("refused", False)
    return text, cites, refused


def claims_with_citations(text: str, cites: list[dict]) -> list[dict]:
    """把带 [n] 的句子与引用元数据配对：每个（句子, 引用）对送审。"""
    pairs = []
    by_n = {c["n"]: c for c in cites}
    # 按句子切，句子内的引用共享该句子作为论断
    pos = 0
    for sent in SENT_SPLIT.split(text):
        start = text.find(sent, pos)
        pos = start + len(sent) if start >= 0 else pos
        ns = [int(m.group(1)) for m in CIT_RE.finditer(sent)]
        if not ns:
            continue
        claim = CIT_RE.sub("", sent).strip()
        # 太短、冒号结尾的引导句不构成可审核论断
        if len(claim) < 12 or claim.endswith(("：", ":", "；", ";", "，", ",")):
            continue
        for n in ns:
            if n in by_n:
                pairs.append({"claim": claim, "cit": by_n[n]})
    return pairs


def main(sample_n: int = 12):
    gpath = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "golden_set.json")
    golden = json.load(open(gpath, encoding="utf-8"))
    import random
    random.seed(42)  # 固定抽样保证跨次可比
    sample = random.sample(golden, min(sample_n, len(golden)))
    print(f"抽样 {len(sample)} 题走全链路回答…")

    total = supported = 0
    answers = 0
    per_q = []
    unsupported_samples: list = []
    for i, g in enumerate(sample, 1):
        try:
            text, cites, refused = sse_answer(g["question"])
        except Exception as e:
            print(f"  [{i}] 请求失败: {type(e).__name__}")
            time.sleep(3)
            continue
        if refused or not cites:
            per_q.append({"question": g["question"], "refused": True, "n_cit": 0, "n_judged": 0, "n_ok": 0})
            print(f"  [{i}/{len(sample)}] 拒答")
            continue
        answers += 1
        pairs = claims_with_citations(text, cites)
        ok = 0
        judged = 0
        for p in pairs:
            verdict = judge(p["claim"], p["cit"]["title"], p["cit"]["page"],
                            p["cit"]["chunk_id"], unsupported_samples)
            if verdict is None:
                continue
            judged += 1
            total += 1
            if verdict:
                supported += 1
                ok += 1
            time.sleep(0.2)
        per_q.append({"question": g["question"], "refused": False,
                      "n_cit": len(cites), "n_judged": judged, "n_ok": ok})
        if judged:
            print(f"  [{i}/{len(sample)}] 引用 {len(pairs)} 对，判定 {judged}，支持 {ok}（{ok/judged:.0%}）")
        else:
            print(f"  [{i}/{len(sample)}] 无句级引用")

    rate = supported / total if total else 0
    print(f"\n===== 引用正确率：{supported}/{total} = {rate:.1%}（门禁 ≥95%）=====")
    print(f"有效回答 {answers}，拒答 {len(sample) - answers}")

    hist_path = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "eval_history.json")
    hist = json.load(open(hist_path, encoding="utf-8")) if os.path.exists(hist_path) else []
    hist.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "type": "answer",
                 "citation_accuracy": round(rate, 4), "total_citations": total,
                 "answers": answers, "refusals": len(sample) - answers, "detail": per_q})
    json.dump(hist, open(hist_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已记录到 {hist_path}")
    if unsupported_samples:
        print("\n----- 不支持样本（供人工复核）-----")
        for u in unsupported_samples:
            print(f"· 论断: {u['claim']}")
            print(f"  证据: {u['chunk']}…")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
