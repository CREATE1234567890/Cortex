# -*- coding: utf-8 -*-
"""构建 golden set：从库内随机抽样证据块，LLM 反向生成问题（答案可在块内找到）。

用法：python scripts/eval_build_golden.py [数量，默认 40]
输出：backend/data/golden_set.json
"""
import json
import os
import random
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.db import db  # noqa: E402
from app.llm import chat_text  # noqa: E402

PROMPT = """根据下面的论文片段写一个具体、自然的问题，要求：
- 答案必须能在该片段中直接找到（不要概括全篇）
- 使用片段主要语言（英文片段写英文问题，中文片段写中文问题）
- 只输出问题本身，不要任何前缀

片段：
{chunk}"""


def main(n: int = 40):
    with db() as conn:
        rows = conn.execute(
            """SELECT c.id, c.doc_id, c.text, c.block_type, d.title
               FROM chunks c JOIN documents d ON d.id = c.doc_id
               WHERE d.status = 'indexed' AND c.block_type != 'table'
               ORDER BY RANDOM()""").fetchall()

    # 每篇文档最多取 2 块，保证覆盖度
    per_doc: dict[str, int] = {}
    picked = []
    for r in rows:
        if per_doc.get(r["doc_id"], 0) >= 2:
            continue
        text = r["text"].strip()
        if len(text) < 200:
            continue
        picked.append(r)
        per_doc[r["doc_id"]] = per_doc.get(r["doc_id"], 0) + 1
        if len(picked) >= n:
            break

    print(f"抽样 {len(picked)} 块（覆盖 {len(per_doc)} 篇文档），开始生成问题…")
    golden = []
    for i, r in enumerate(picked, 1):
        try:
            q = chat_text([{"role": "user", "content": PROMPT.format(chunk=r["text"][:1800])}],
                          temperature=0.3)
            q = q.strip().strip('"').splitlines()[0][:200]
            if not q or len(q) < 8 or re.search(r"片段|论文片段", q):
                continue
            golden.append({
                "chunk_id": r["id"], "doc_id": r["doc_id"],
                "doc_title": r["title"], "question": q,
            })
            print(f"  [{i}/{len(picked)}] {q[:60]}")
        except Exception as e:
            print(f"  [{i}] 生成失败: {type(e).__name__}")
            time.sleep(2)

    out = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "golden_set.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(golden, f, ensure_ascii=False, indent=1)
    print(f"完成：{len(golden)} 条 → {out}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 40)
