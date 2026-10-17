# -*- coding: utf-8 -*-
"""为存量文档回填 venue（会议/期刊）与 category（文件夹类别）。

用法：python scripts/classify_backfill.py
可直接对运行中的服务使用（SQLite WAL 支持并发；只更新分类字段）。
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.classify import classify, venue_from_text, venue_from_cover  # noqa: E402
from app.config import get_kb_root  # noqa: E402
from app.db import db  # noqa: E402


def main():
    root = get_kb_root()
    with db() as conn:
        rows = conn.execute(
            "SELECT id, title, filename, pdf_path, venue, category FROM documents"
        ).fetchall()
    print(f"待处理 {len(rows)} 篇（根目录：{root}）")
    t0 = time.time()
    updated = 0
    stats = {}
    for i, r in enumerate(rows, 1):
        doc_id, title, filename, pdf_path, venue, category = (
            r["id"], r["title"], r["filename"], r["pdf_path"], r["venue"], r["category"])
        # 全量重算（规则更新后覆盖旧值）；category 确定性好，直接重算
        cat, ven = classify(pdf_path, root, title or "")
        if cat != category or (ven or None) != venue:
            with db() as conn:
                conn.execute("UPDATE documents SET venue=?, category=? WHERE id=?",
                             (ven or None, cat, doc_id))
            updated += 1
        key = ven or "（未识别）"
        stats[key] = stats.get(key, 0) + 1
        if i % 40 == 0:
            print(f"  … {i}/{len(rows)}")
    print(f"完成：更新 {updated} 篇，用时 {time.time()-t0:.0f}s")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {k:24} {v}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main()
