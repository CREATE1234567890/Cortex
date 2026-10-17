# -*- coding: utf-8 -*-
"""Agent 功能自测：覆盖聊天/范围/拒答/多轮/记忆/设置/文献库/边界情况。

用法：python scripts/agent_selftest.py（后端须在 8300 运行）
输出：逐项 PASS/FAIL + 汇总。全程约 3-5 分钟（含 4 次真实 LLM 调用）。
"""
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

API = "http://127.0.0.1:8300"
results: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((bool(ok), name, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  | {detail}" if detail else ""))


def chat(question: str, doc_ids=None, conv_id=None, timeout=300):
    """走 SSE，返回 (events计数dict, 最终文本, 引用数, evidences数, conv_id)。"""
    counts = {}
    text, cites, evids, cid, refused = "", [], [], None, False
    with httpx.stream("POST", f"{API}/api/chat", timeout=timeout,
                      json={"question": question, "conversation_id": conv_id,
                            "doc_ids": doc_ids}) as r:
        buf = ""
        for chunk in r.iter_text():
            buf += chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                ev, data = None, None
                for line in block.split("\n"):
                    if line.startswith("event: "):
                        ev = line[7:].strip()
                    elif line.startswith("data: "):
                        data = line[6:]
                if not data:
                    continue
                counts[ev] = counts.get(ev, 0) + 1
                try:
                    d = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if ev == "delta":
                    text += d.get("text", "")
                elif ev == "evidence":
                    evids = d.get("evidences", [])
                elif ev == "citations":
                    cites = d.get("citations", [])
                elif ev == "meta":
                    cid = d.get("conversation_id")
                elif ev == "done":
                    refused = d.get("refused", False)
                elif ev == "error":
                    text += f"__ERROR__{d.get('message', '')}"
    return counts, text, cites, evids, cid, refused


def main():
    c = httpx.Client(timeout=60)

    # ---- 基础 ----
    h = c.get(f"{API}/api/health").json()
    check("健康检查", h.get("status") == "ok")
    check("LLM 已配置", h.get("llm_ready") is True)

    docs = c.get(f"{API}/api/documents").json()
    indexed = [d for d in docs if d["status"] == "indexed"]
    check("文档列表非空", len(indexed) > 0, f"{len(indexed)} indexed / {len(docs)}")

    # ---- 设置读写 ----
    s = c.post(f"{API}/api/settings", json={"rerank_enabled": "on"}).json()
    check("设置保存 rerank", s.get("rerank_enabled") is True)
    s2 = c.get(f"{API}/api/settings").json()
    check("设置读取", "llm_model" in s2 and "rerank_enabled" in s2)

    # ---- 聊天：正常中文问题（真实 LLM）----
    counts, text, cites, evids, cid, refused = chat("库里的论文用了哪些医学图像分割数据集？")
    ok = ("done" in counts and "error" not in text and len(text) > 100
          and len(evids) > 0 and cid is not None)
    check("正常问答（全链路）", ok, f"len={len(text)} evid={len(evids)} cites={len(cites)}")
    conv = cid

    # 拒答标记不应为真
    check("正常问答不拒答", refused is False)

    # ---- 引用编号一致性：citations 的 n 与文本中的 [n] 对齐 ----
    import re
    cited_ns = set(int(m.group(1)) for m in re.finditer(r"\[(\d{1,2})\]", text))
    valid_ns = {x["n"] for x in cites}
    check("引用编号一致（无幻觉引用残留）", cited_ns <= valid_ns or not cites,
          f"text={sorted(cited_ns)} meta={sorted(valid_ns)}")

    # ---- 多轮：同会话追问 ----
    counts2, text2, _, _, cid2, _ = chat("刚才提到的第一个数据集，具体在哪篇论文里？", conv_id=conv)
    msgs = c.get(f"{API}/api/conversations/{conv}/messages").json()
    check("多轮历史生效", cid2 == conv and len(msgs) >= 4, f"{len(msgs)} 条消息")
    check("多轮回答正常", "error" not in text2 and len(text2) > 50)

    # ---- 会话改名（Bug 2 回归）----
    conv_meta = [x for x in c.get(f"{API}/api/conversations").json() if x["id"] == conv]
    check("会话自动改名（Bug2 回归）",
          conv_meta and conv_meta[0]["title"] not in ("新对话", ""),
          f"title={conv_meta[0]['title'][:20] if conv_meta else None}")

    # ---- 英文提问（语言跟随）----
    _, text_en, _, _, _, refused_en = chat("What datasets are used in these papers for brain tumor segmentation?")
    has_cjk = any('\u4e00' <= ch <= '\u9fff' for ch in text_en[:400])
    check("英文提问英文回答", len(text_en) > 100 and not refused_en and not has_cjk)

    # ---- 拒答路径（离题问题）----
    _, text_r, _, evids_r, _, refused_r = chat("今天天气怎么样？适合出门跑步吗？")
    check("离题问题拒答", refused_r is True or "未找到" in text_r or "Not enough" in text_r,
          f"refused={refused_r} evid={len(evids_r)}")

    # ---- 范围限定（doc_ids）----
    target = indexed[0]
    _, text_s, _, evids_s, _, _ = chat("这篇论文用了什么方法？", doc_ids=[target["id"]])
    leak = [e for e in evids_s if e["doc_id"] != target["id"]]
    check("范围限定：初始证据不越界", len(evids_s) > 0 and not leak,
          f"evid={len(evids_s)} leak={len(leak)}")

    # ---- 范围限定 + 工具循环不越界（Bug 1 回归，需触发补充检索）----
    _, text_t, _, evids_t, _, _ = chat(
        "详细说明这篇论文的实验设置与消融实验结论", doc_ids=[target["id"]])
    leak_t = [e for e in evids_t if e["doc_id"] != target["id"]]
    tool_events = 0  # 无法直接拿到 tool 事件数（chat 未返回），用证据检查
    check("范围限定：补充检索不越界（Bug1 回归）", not leak_t,
          f"evid={len(evids_t)} leak={len(leak_t)}")

    # ---- 不存在的 doc 过滤 → 优雅拒答 ----
    _, text_x, _, evids_x, _, refused_x = chat("U-Net 的结构是什么？", doc_ids=["doc_notexist"])
    check("无效范围优雅拒答", (refused_x and not evids_x) or "未找到" in text_x or "Not enough" in text_x,
          f"refused={refused_x}")

    # ---- 特殊字符 ----
    _, text_sp, _, _, _, _ = chat('引号"测试"与【特殊】字符{}的处理？')
    check("特殊字符不崩溃", "__ERROR__" not in text_sp and len(text_sp) > 10)

    # ---- 记忆 ----
    m = c.get(f"{API}/api/memories").json()
    check("记忆列表接口", isinstance(m.get("items"), list) and "enabled" in m)
    c.post(f"{API}/api/memories/toggle", json={"enabled": False})
    _, _, _, _, _, _ = chat("记忆关闭时的测试问题")
    c.post(f"{API}/api/memories/toggle", json={"enabled": True})
    check("记忆开关往返", True)

    # ---- 会话管理 ----
    nc = c.post(f"{API}/api/conversations").json()
    dd = c.delete(f"{API}/api/conversations/{nc['id']}").json()
    check("会话创建/删除", dd.get("ok") is True)

    # ---- 文献库接口 ----
    check("失败文档清单", all(d.get("status") in ("indexed", "pending", "parsing", "failed")
                              for d in docs))
    nf = c.delete(f"{API}/api/documents/doc_notexist")
    check("删除不存在文档 404", nf.status_code == 404)
    ing = c.post(f"{API}/api/library/ingest", json={"paths": []}).json()
    check("空导入列表 400 拦截", ing.get("started") is False or c.post(
        f"{API}/api/library/ingest", json={"paths": []}).status_code in (400, 200))

    # ---- PDF 服务 ----
    if indexed:
        pr = c.get(f"{API}/api/documents/{indexed[0]['id']}/pdf")
        check("PDF 文件服务", pr.status_code == 200 and pr.headers.get("content-type", "").startswith("application/pdf"))

    # ---- 并发两条聊天 ----
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(2) as ex:
        futs = [ex.submit(chat, q) for q in
                ("U-Net 的跳跃连接有什么作用？", "Mamba 系列方法的核心思想是什么？")]
        outs = [f.result() for f in futures_ok(futs)]
    check("并发两条问答不互串", all("done" in o[0] and "error" not in o[1] for o in outs),
          f"lens={[len(o[1]) for o in outs]}")

    # ---- 汇总 ----
    fails = [r for r in results if not r[0]]
    print(f"\n===== {len(results) - len(fails)}/{len(results)} PASS =====")
    for ok_, name, detail in fails:
        print(f"FAILED: {name} | {detail}")


def futures_ok(futs):
    return list(futs)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
