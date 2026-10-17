# -*- coding: utf-8 -*-
"""检索质量回归：golden set 上跑 Recall@8 / MRR@8，对比 rerank 开/关，存历史。

用法：python scripts/eval_run.py
历史：backend/data/eval_history.json（可对比历次跑分，防回退）
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.retriever import retrieve  # noqa: E402
from app.db import db, set_setting, get_setting  # noqa: E402

HISTORY = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "eval_history.json")


def run_mode(golden: list[dict], mode: str) -> dict:
    set_setting("rerank_enabled", "on" if mode == "rerank" else "off")
    hit = doc_hit = 0
    rr_sum = 0.0
    t0 = time.time()
    for i, g in enumerate(golden, 1):
        evs = retrieve(g["question"], top_k=8)
        ids = [e.chunk_id for e in evs]
        rank = ids.index(g["chunk_id"]) + 1 if g["chunk_id"] in ids else 0
        if rank:
            hit += 1
            rr_sum += 1 / rank
        if any(e.doc_id == g["doc_id"] for e in evs):
            doc_hit += 1
        print(f"\r  [{i}/{len(golden)}]", end="", flush=True)
    dt = time.time() - t0
    n = len(golden)
    print()
    return {"mode": mode, "n": n,
            "hit8": round(hit / n, 4), "doc_hit8": round(doc_hit / n, 4),
            "mrr8": round(rr_sum / n, 4),
            "avg_latency_s": round(dt / n, 2), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}


def main():
    path = os.path.join(os.path.dirname(__file__), "..", "backend", "data", "golden_set.json")
    golden = json.load(open(path, encoding="utf-8"))
    print(f"golden set: {len(golden)} 条")

    prev_setting = get_setting("rerank_enabled", "on")
    results = []
    for mode in ("off", "on"):
        print(f"== rerank {mode} ==")
        results.append(run_mode(golden, "rerank" if mode == "on" else "baseline"))
    set_setting("rerank_enabled", prev_setting)  # 恢复原设置

    print("\n===== 对比 =====")
    base, rr = results[0], results[1]
    print(f"{'':12} {'Recall@8':>10} {'DocHit@8':>10} {'MRR@8':>10} {'延迟(s)':>8}")
    for r in results:
        print(f"{r['mode']:12} {r['hit8']:>10} {r['doc_hit8']:>10} {r['mrr8']:>10} {r['avg_latency_s']:>8}")
    delta = rr["hit8"] - base["hit8"]
    print(f"\nRerank 带来的 Recall@8 变化: {delta:+.1%}")

    hist = []
    if os.path.exists(HISTORY):
        hist = json.load(open(HISTORY, encoding="utf-8"))
    hist.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results})
    json.dump(hist, open(HISTORY, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已记录到 {HISTORY}（共 {len(hist)} 次评估）")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
