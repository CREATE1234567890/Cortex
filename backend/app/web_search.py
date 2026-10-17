"""在线学术检索（arXiv + Semantic Scholar）：库外来源，免费 API。

设计约束：
- 全部 fail-soft：网络失败返回空列表，绝不阻塞问答主流程
- 结果统一为 {title, summary, year, source, url}，与本地证据体系隔离
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

import httpx

_TIMEOUT = 8.0
_ATOM = "{http://www.w3.org/2005/Atom}"


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    """arXiv 官方 API（Atom）。无需 Key。"""
    out: list[dict] = []
    try:
        r = httpx.get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f"all:{query}", "max_results": max_results,
                    "sortBy": "relevance"},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.text)
        for entry in root.findall(f"{_ATOM}entry"):
            url = (entry.findtext(f"{_ATOM}id") or "").strip()
            title = _clean(entry.findtext(f"{_ATOM}title") or "")
            summary = _clean(entry.findtext(f"{_ATOM}summary") or "")[:900]
            published = (entry.findtext(f"{_ATOM}published") or "")[:4]
            authors = [_clean(a.findtext(f"{_ATOM}name") or "")
                       for a in entry.findall(f"{_ATOM}author")][:6]
            if title and summary:
                out.append({"source": "arXiv", "title": title, "summary": summary,
                            "year": published, "url": url, "authors": authors})
    except Exception:
        pass
    return out


def search_s2(query: str, max_results: int = 5) -> list[dict]:
    """Semantic Scholar 论文搜索（匿名，429 时静默放弃）。"""
    out: list[dict] = []
    try:
        r = httpx.get(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            params={"query": query[:300], "limit": max_results,
                    "fields": "title,abstract,year,venue,externalIds,url"},
            timeout=_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        for p in (r.json().get("data") or [])[:max_results]:
            title = _clean(p.get("title") or "")
            abstract = _clean(p.get("abstract") or "")[:900]
            if not title:
                continue
            url = p.get("url") or (p.get("externalIds") or {}).get("DOI") and \
                f"https://doi.org/{p['externalIds']['DOI']}" or ""
            venue = p.get("venue") or ""
            out.append({"source": venue[:40] or "Semantic Scholar", "title": title,
                        "summary": abstract or "（无摘要）", "year": str(p.get("year") or ""),
                        "url": url, "authors": []})
    except Exception:
        pass
    return out


def web_search(query: str, max_results: int = 5) -> list[dict]:
    """合并去重（按标题词重叠）。arXiv 在前（有预印本全文链接）。"""
    results = search_arxiv(query, max_results)
    seen_keys = [{w.lower() for w in re.findall(r"\w{4,}", r_["title"])} for r_ in results]
    for r_ in search_s2(query, max_results):
        key = {w.lower() for w in re.findall(r"\w{4,}", r_["title"])}
        if any(len(key & s) / max(1, len(key | s)) > 0.7 for s in seen_keys):
            continue
        results.append(r_)
        seen_keys.append(key)
        if len(results) >= max_results:
            break
    return results[:max_results]
