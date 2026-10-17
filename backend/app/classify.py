"""论文来源与类别识别（FR-KB-02 的零成本启发式版）。

venue：会议（CVPR/ICCV/ECCV/NeurIPS/AAAI/ICML/ICLR/MICCAI/WACV…）、期刊（文件名后缀/常见期刊名）、arXiv。
category：知识库根目录下的一级子文件夹（AAAI/cvpr/micca/其他论文…）。

策略：文件名/标题正则 → 命不中时扫描 PDF 首页文本（仅第 1 页，快速）。
CrossRef/Semantic Scholar 精确元数据补全留给 V1。
"""
from __future__ import annotations

import re
from pathlib import Path

# 会议正则 → 规范名（大小写不敏感）
_CONFERENCE_PATTERNS: list[tuple[str, str]] = [
    (r"\bCVPR\b|Computer Vision and Pattern Recognition", "CVPR"),
    (r"\bICCV\b|International Conference on Computer Vision", "ICCV"),
    (r"\bECCV\b|European Conference on Computer Vision", "ECCV"),
    (r"\bNeurIPS\b|\bNIPS\b|Neural Information Processing Systems", "NeurIPS"),
    (r"\bAAAI\b", "AAAI"),
    (r"\bICML\b|International Conference on Machine", "ICML"),
    (r"\bICLR\b|International Conference on Learning Representations", "ICLR"),
    (r"\bNAACL\b|Annual Meeting of the Association for Computational", "NAACL"),
    (r"\bMICCAI\b", "MICCAI"),
    (r"\bWACV\b", "WACV"),
    (r"\bISBI\b", "ISBI"),
    (r"\bEMBC\b", "EMBC"),
    (r"\bIJCAI\b", "IJCAI"),
    (r"\bACMMM\b|\bACMFMM\b|ACM Multimedia", "ACM MM"),
]

# 期刊关键词（用于从文件名“标题_期刊名”模式及标题中识别）
_JOURNAL_HINTS = [
    "IEEE Transactions", "IEEE TMI", "Medical Image Analysis", "MedIA",
    "Pattern Recognition", "Nature", "Science",
    "学报", "进展", "Journal", "Transactions", "Letters", "Review", "期刊",
]

_ARXIV_RE = re.compile(r"arXiv[:\s]*\d{4}\.\d{4,5}|\b\d{4}\.\d{4,5}(?:v\d+)?\b")


def _is_plausible_venue(venue: str | None) -> bool:
    """过滤误识别：被当成期刊的论文标题片段、截断的水印文本。"""
    if not venue:
        return False
    v = venue.strip()
    if v in ("Journal Pre-proo", "Journal Pre-proof"):
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", v))
    # 中文：期刊名一般 ≤12 字；含“研究/综述”的多为论文标题
    if has_cjk and (len(v) > 12 or re.search(r"研究|综述|方法|识别|分割", v)):
        return False
    # 英文：>40 字符且含多个小写单词的多为截断的标题/长句
    if not has_cjk and len(v) >= 30 and len(v.split()) >= 5:
        return False
    return True


def venue_from_text(*texts: str) -> str | None:
    """从文件名/标题等文本识别会议或期刊名。优先会议，其次期刊/arXiv。"""
    blob = " ".join(t for t in texts if t)
    for pat, name in _CONFERENCE_PATTERNS:
        if re.search(pat, blob, re.I):
            return name
    if _ARXIV_RE.search(blob):
        return "arXiv"
    # 文件名“标题_期刊名”模式：最后一个下划线后的中文段
    for t in texts:
        if not t:
            continue
        parts = re.split(r"[_(]", Path(t).stem if t.endswith(".pdf") else t)
        for part in reversed(parts):
            part = part.strip(" ).pdf")
            if len(part) >= 4 and any(h.lower() in part.lower() for h in _JOURNAL_HINTS) and not re.search(r"\d{4}", part):
                return part[:40]
    # 标题中直接含期刊关键词
    for t in texts:
        if not t:
            continue
        for h in ("IEEE Transactions", "Medical Image Analysis", "Pattern Recognition"):
            if h.lower() in t.lower():
                return h
    return None


def venue_from_cover(pdf_path: str) -> str | None:
    """扫描 PDF 首页文本识别出处（仅第 1 页，快速；失败返回 None）。"""
    try:
        import pymupdf as fitz
        with fitz.open(pdf_path) as doc:
            if doc.page_count == 0:
                return None
            text = doc[0].get_text("text")[:4000]
        m = re.search(r"arXiv[:\s]*(\d{4}\.\d{4,5})", text)
        if m:
            return "arXiv"
        for pat, name in _CONFERENCE_PATTERNS:
            if re.search(pat, text, re.I):
                return name
        m = re.search(r"((?:Proceedings of|In:)[^\n]{5,90})", text)
        if m:
            captured = m.group(1).strip()
            for pat, name in _CONFERENCE_PATTERNS:
                if re.search(pat, captured, re.I):
                    return name
            return captured[:60]
        m = re.search(r"(©|Copyright|©\s*)\s*(20\d{2})\s*(IEEE|Elsevier|Springer|ACM)", text)
        if m:
            return f"{m.group(3)}"
    except Exception:
        pass
    return None


def category_from_path(pdf_path: str, kb_root: str) -> str:
    """类别 = 相对知识库根目录的一级子文件夹；根目录文件归为“根目录”。"""
    try:
        root = Path(kb_root).resolve()
        p = Path(pdf_path).resolve()
        rel = p.relative_to(root)
        parts = rel.parts
        if len(parts) <= 1:
            return "根目录"
        return parts[0]
    except Exception:
        parent = Path(pdf_path).parent.name
        return parent or "根目录"


def classify(pdf_path: str, kb_root: str, title: str) -> tuple[str, str]:
    """返回 (category, venue)。venue 依次尝试 文本正则 → 首页扫描，末尾做合理性过滤。"""
    category = category_from_path(pdf_path, kb_root)
    venue = venue_from_text(Path(pdf_path).name, title) or venue_from_cover(pdf_path) or ""
    if not _is_plausible_venue(venue):
        venue = ""
    return category, venue
