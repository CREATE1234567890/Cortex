# -*- coding: utf-8 -*-
"""元数据补全（FR-KB-02）：按标题查 Semantic Scholar，回填 venue/year/authors/DOI。

策略：
- 只处理缺失项：venue 为空、或 year 为空、或 authors 为空的文档
- Semantic Scholar Graph API（无需 Key，1 请求/1.1s 礼貌限速）
- 标题相似度 ≥0.75 才采纳（避免同名错配）
- venue 用 classify._is_plausible_venue 复核

用法：python scripts/enrich_metadata.py [上限，默认全部缺失文档]
"""
import json
import os
import sys
import time

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.classify import _is_plausible_venue  # noqa: E402
from app.db import db  # noqa: E402

S2 = "https://api.semanticscholar.org/graph/v1/paper/search"
FIELDS = "title,year,venue,externalIds,authors"


def norm(t: str) -> str:
    import re
    t = re.sub(r"[^\w\s]", "", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def similar(a: str, b: str) -> float:
    A, B = set(norm(a).split()), set(norm(b).split())
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def lookup(client: httpx.Client, title: str) -> dict | None:
    try:
        r = client.get(S2, params={"query": title[:300], "fields": FIELDS, "limit": 3},
                       timeout=30)
        if r.status_code == 429:
            time.sleep(5)
            return None
        data = r.json().get("data") or []
    except Exception:
        time.sleep(3)
        return None
    best, best_sim = None, 0.0
    for p in data:
        sim = similar(title, p.get("title") or "")
        if sim > best_sim:
            best, best_sim = p, sim
    return best if best and best_sim >= 0.75 else None


def main(limit: int | None = None):
    with db() as conn:
        rows = conn.execute(
            """SELECT id, title, pdf_path, year, venue, authors, doi FROM documents
               WHERE (venue IS NULL OR venue = '' OR year IS NULL OR authors IS NULL)
               AND status = 'indexed'""").fetchall()
    if limit:
        rows = rows[:limit]
    print(f"待补全 {len(rows)} 篇")

    updated = venue_n = year_n = author_n = doi_n = 0
    with httpx.Client() as client:
        for i, r in enumerate(rows, 1):
            p = lookup(client, r["title"])
            time.sleep(1.1)
            if not p:
                continue
            sets, vals = [], []
            new_venue = (p.get("venue") or "").strip()
            if not r["venue"] and new_venue and _is_plausible_venue(new_venue):
                sets.append("venue=?"); vals.append(new_venue[:60]); venue_n += 1
            if not r["year"] and p.get("year"):
                sets.append("year=?"); vals.append(int(p["year"])); year_n += 1
            if not r["authors"] and p.get("authors"):
                names = [a["name"] for a in p["authors"][:8]]
                if names:
                    sets.append("authors=?"); vals.append(json.dumps(names, ensure_ascii=False))
                    author_n += 1
            doi = (p.get("externalIds") or {}).get("DOI")
            if not r["doi"] and doi:
                sets.append("doi=?"); vals.append(doi); doi_n += 1
            if sets:
                vals.append(r["id"])
                with db() as conn:
                    conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id=?", vals)
                updated += 1
            if i % 20 == 0:
                print(f"  … {i}/{len(rows)}（更新 {updated}）")
    print(f"完成：更新 {updated} 篇 ｜ venue +{venue_n}  year +{year_n}  authors +{author_n}  doi +{doi_n}")


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
