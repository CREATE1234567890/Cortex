"""PDF 解析：PyMuPDF 快速通道（对应架构 ADR-2 的 CPU 通道）。

输出带坐标的内容块序列，保证三件事：
1. 每个块携带页码 + bbox —— 页码级引用的物理基础；
2. 双栏论文按"栏带"重排阅读顺序（全宽块切分栏带，带内先左栏后右栏）；
3. 标题层级识别 → section_path；摘要/图表标题/参考文献区分类。

块类型: heading / text / caption / table / reference / abstract
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf as fitz  # PyMuPDF

# ---------- 正则 ----------
REF_HEADING_RE = re.compile(r"^\s*(references|bibliography|参考文献|参\s*考\s*文\s*献)\s*:?\s*$", re.I)
ABSTRACT_RE = re.compile(r"^\s*(abstract|摘要)\s*[—:.：]?\s*", re.I)
CAPTION_RE = re.compile(r"^\s*(figure|fig\.?|table|图|表)\s*\d+", re.I)
NUMBERED_HEADING_RE = re.compile(r"^\s*(\d+(?:\.\d+)*)[\.、]?\s+(\S.{0,120})$")
UNNUMBERED_HEADING_RE = re.compile(
    r"^\s*(abstract|introduction|background|related works?|preliminaries|methods?|methodology|"
    r"experiments?|results?|discussion|conclusions?|acknowledg(e)?ments?|appendix|"
    r"引言|背景|相关工作|预备知识|方法|实验|结果|讨论|结论|致谢|附录)\s*:?\s*$",
    re.I,
)


@dataclass
class ParsedBlock:
    page: int                      # 0-based
    bbox: tuple                    # (x0, y0, x1, y1) PDF 坐标
    text: str
    kind: str = "text"             # heading/text/caption/table/reference
    level: int | None = None       # heading 层级 1..3
    spans_meta: dict = field(default_factory=dict)


@dataclass
class ParsedDoc:
    blocks: list[ParsedBlock]
    n_pages: int
    title: str
    year: int | None
    body_font_size: float


def _clean_text(text: str) -> str:
    """行拼接与断词修复：拉丁连字符行尾去 '-'；中文直接拼接，英文补空格。"""
    text = text.replace("\u00ad", "")  # soft hyphen
    lines = [ln.strip() for ln in text.splitlines()]
    out = ""
    for ln in lines:
        if not ln:
            continue
        if not out:
            out = ln
            continue
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln
        elif out[-1].isascii() and out[-1].isalnum() and ln[:1].isascii() and ln[:1].isalnum():
            out += " " + ln
        else:
            out += ln
    return out.strip()


def _iter_spans(page):
    d = page.get_text("dict")
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        yield block


def _raw_blocks(page) -> list[dict]:
    """把 page 的文本行聚成 raw blocks（含 bbox、最大字号、加粗标志）。"""
    d = page.get_text("dict")
    out = []
    for block in d.get("blocks", []):
        if block.get("type") != 0:
            continue
        lines = []
        max_size = 0.0
        bold = False
        for line in block.get("lines", []):
            if tuple(line.get("dir", (1, 0))) != (1.0, 0.0):
                continue  # 跳过竖排/旋转文本（arXiv 侧边水印等）
            line_text = ""
            for span in line.get("spans", []):
                t = span.get("text", "")
                line_text += t
                size = float(span.get("size", 0))
                max_size = max(max_size, size)
                if "Bold" in span.get("font", "") or span.get("flags", 0) & 16:
                    bold = True
            if line_text.strip():
                lines.append(line_text)
        text = _clean_text("\n".join(lines))
        if text:
            out.append({"bbox": tuple(block["bbox"]), "text": text,
                        "size": max_size, "bold": bold})
    return out


def _is_two_column(blocks: list[dict], page_width: float) -> bool:
    """块起点直方图判断双栏：左缘聚在左边距与页面中部两处。"""
    if len(blocks) < 6 or page_width <= 0:
        return False
    mid = page_width * 0.5
    left = sum(1 for b in blocks if b["bbox"][0] < page_width * 0.18)
    center = sum(1 for b in blocks if mid - page_width * 0.08 < b["bbox"][0] < mid + page_width * 0.06)
    wide = sum(1 for b in blocks if (b["bbox"][2] - b["bbox"][0]) > page_width * 0.75)
    narrow = len([b for b in blocks if (b["bbox"][2] - b["bbox"][0]) <= page_width * 0.55])
    return center >= 3 and narrow >= 4 and wide <= narrow * 0.5


def _reorder_two_column(blocks: list[dict], page_width: float) -> list[dict]:
    """全宽块（标题/图表说明）作为分带屏障，带内先左栏后右栏。"""
    full, cols = [], []
    for b in blocks:
        (full if (b["bbox"][2] - b["bbox"][0]) > page_width * 0.7 else cols).append(b)
    if not cols:
        return blocks
    bands: list[list[dict]] = []
    anchors = sorted(full, key=lambda b: b["bbox"][1])
    band_edges = [a["bbox"][3] for a in anchors]
    for b in sorted(cols, key=lambda b: b["bbox"][1]):
        # 找到该块顶部所属的带：之前所有底部小于块顶的全宽块
        idx = 0
        for i, e in enumerate(band_edges):
            if e <= b["bbox"][1]:
                idx = i + 1
        while len(bands) <= idx:
            bands.append([])
        bands[idx].append(b)
    mid = page_width * 0.5
    ordered: list[dict] = []
    anchor_iter = iter(sorted(range(len(anchors)), key=lambda i: anchors[i]["bbox"][1]))
    band_i = 0
    used_anchor = 0
    result: list[dict] = []
    # 交织输出：带0栏块 → 全宽块0 → 带1栏块 → 全宽块1 ...
    anchors_sorted = sorted(anchors, key=lambda b: b["bbox"][1])
    for i, band in enumerate(bands):
        left = [b for b in band if b["bbox"][2] <= mid + page_width * 0.02]
        right = [b for b in band if b["bbox"][2] > mid + page_width * 0.02]
        left.sort(key=lambda b: b["bbox"][1])
        right.sort(key=lambda b: b["bbox"][1])
        result.extend(left)
        result.extend(right)
        if i < len(anchors_sorted):
            result.append(anchors_sorted[i])
    # 全宽块数量可能多于带数，剩余的按位置插入末尾
    if len(anchors_sorted) > len(bands):
        result.extend(anchors_sorted[len(bands):])
    return result


def _table_blocks(page) -> list[dict]:
    """表格区域单独抽出（markdown 化），供整块独立成 chunk。"""
    out = []
    try:
        tabs = page.find_tables()
        for t in tabs.tables:
            try:
                md = t.to_markdown(clean=False)
            except Exception:
                md = t.extract() and "\n".join(
                    " | ".join("" if c is None else str(c) for c in row) for row in t.extract()
                )
            if md and md.strip():
                out.append({"bbox": tuple(t.bbox), "text": md.strip()})
    except Exception:
        pass
    return out


def _overlap_ratio(a, b) -> float:
    x0, y0, x1, y1 = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    area = (a[2] - a[0]) * (a[3] - a[1])
    return inter / area if area else 0.0


def _drop_headers_footers(blocks_by_page: dict[int, list[dict]]):
    """跨页重复的页眉/页脚剔除。"""
    if len(blocks_by_page) < 3:
        return
    from collections import Counter
    edges: list[tuple[str, int]] = []  # (normalized text, count)
    counter: Counter = Counter()
    for blocks in blocks_by_page.values():
        if not blocks:
            continue
        for b in (blocks[0], blocks[-1]):
            t = re.sub(r"\d+", "#", b["text"]).strip().lower()
            if 3 < len(t) < 120:
                counter[t] += 1
    repeated = {t for t, c in counter.items() if c >= max(3, len(blocks_by_page) * 0.6)}
    for blocks in blocks_by_page.values():
        blocks[:] = [
            b for b in blocks
            if re.sub(r"\d+", "#", b["text"]).strip().lower() not in repeated
        ]


def parse_pdf(path: str) -> ParsedDoc:
    doc = fitz.open(path)
    n_pages = doc.page_count

    # 页眉页脚过滤需要先收集，再逐页重排
    raw_by_page: dict[int, list[dict]] = {}
    tables_by_page: dict[int, list[dict]] = {}
    for i in range(n_pages):
        page = doc[i]
        blocks = _raw_blocks(page)
        raw_by_page[i] = blocks
        tables_by_page[i] = _table_blocks(page)

    _drop_headers_footers(raw_by_page)

    # 正文字号：所有块字号的长度加权众数近似（取中位数）
    all_sizes = []
    for blocks in raw_by_page.values():
        for b in blocks:
            all_sizes.extend([b["size"]] * max(1, len(b["text"]) // 40))
    body_size = float(sorted(all_sizes)[len(all_sizes) // 2]) if all_sizes else 10.0

    parsed: list[ParsedBlock] = []
    in_references = False
    title = (doc.metadata.get("title") or "").strip()
    year = _guess_year(doc)

    for i in range(n_pages):
        page = doc[i]
        pw = page.rect.width
        blocks = raw_by_page[i]
        if not blocks:
            continue
        if _is_two_column(blocks, pw):
            blocks = _reorder_two_column(blocks, pw)
        else:
            blocks.sort(key=lambda b: (round(b["bbox"][1] / 10), b["bbox"][0]))

        tables = tables_by_page.get(i, [])
        for b in blocks:
            # 与表格区域高度重叠的文本块丢弃（表格已单独成块）
            if any(_overlap_ratio(b["bbox"], t["bbox"]) > 0.5 for t in tables):
                continue
            kind = "text"
            level = None
            if REF_HEADING_RE.match(b["text"]):
                in_references = True
                parsed.append(ParsedBlock(i, b["bbox"], b["text"], "heading", 1))
                continue
            if in_references:
                parsed.append(ParsedBlock(i, b["bbox"], b["text"], "reference"))
                continue
            is_heading = _heading_like(b, body_size)
            if is_heading:
                kind, level = "heading", _heading_level(b, body_size)
            elif CAPTION_RE.match(b["text"]) and len(b["text"]) < 400:
                kind = "caption"
            elif ABSTRACT_RE.match(b["text"]):
                kind = "abstract"
            parsed.append(ParsedBlock(i, b["bbox"], b["text"], kind, level,
                                      {"size": b["size"], "bold": b["bold"]}))
        for t in tables:
            parsed.append(ParsedBlock(i, t["bbox"], t["text"], "table"))

    parsed.sort(key=lambda b: (b.page,))  # 页内顺序已在重排时确定（稳定排序保留）
    if not title:
        title = _guess_title(parsed)
    doc.close()
    return ParsedDoc(blocks=parsed, n_pages=n_pages, title=title, year=year,
                     body_font_size=body_size)


def _heading_like(b: dict, body_size: float) -> bool:
    text = b["text"]
    if len(text) > 130 or text.endswith((".", "。", ";", "；", ",", "，")):
        return False
    numbered = bool(NUMBERED_HEADING_RE.match(text))
    unnumbered = bool(UNNUMBERED_HEADING_RE.match(text))
    big = b["size"] >= body_size * 1.15
    return (numbered and b["bold"]) or (numbered and big) or unnumbered or (big and b["bold"] and len(text) < 90)


def _heading_level(b: dict, body_size: float) -> int:
    m = NUMBERED_HEADING_RE.match(b["text"])
    if m:
        return min(m.group(1).count(".") + 1, 3)
    if b["size"] >= body_size * 1.35:
        return 1
    if b["size"] >= body_size * 1.18:
        return 2
    return 3


def _guess_year(doc) -> int | None:
    for src in (doc.metadata.get("creationDate") or "", doc.metadata.get("modDate") or ""):
        m = re.search(r"(19|20)\d{2}", src)
        if m:
            return int(m.group(0))
    return None


def _guess_title(blocks: list[ParsedBlock]) -> str:
    """首页最大字号的短文本块 ≈ 标题。"""
    first_page = [b for b in blocks if b.page == 0 and b.kind != "reference"]
    if not first_page:
        return "未知标题"
    best = max(first_page, key=lambda b: b.spans_meta.get("size", 0))
    t = best.text.replace("\n", " ")
    return t[:200]
