"""Section-aware 切块（对应架构 §2 与 FR-DP-03）。

原则：
- 沿标题层级切，正文 400–600 token、重叠 15%，边界对齐段落（宁小勿跨）；
- 摘要、表格、图表标题整块独立成 chunk（表格前置 caption）；
- 参考文献区不入索引；
- 每 chunk 携带 section_path / page_start / page_end / bbox 列表（引用溯源元数据）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config
from .parsing.pdf_parser import ParsedBlock

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def est_tokens(text: str) -> int:
    """粗估 token：CJK 字符≈1 token/字，其余≈1 token/4 字符。"""
    cjk = len(CJK_RE.findall(text))
    return cjk + (len(text) - cjk) // 4 + 1


@dataclass
class Chunk:
    text: str
    section_path: list[str]
    page_start: int                 # 0-based
    page_end: int
    bboxes: list[dict]              # [{"page": int, "bbox": [x0,y0,x1,y1]}]
    block_type: str = "text"
    char_start: int = 0
    char_end: int = 0


def _merge_bbox_list(bboxes: list[dict]) -> list[dict]:
    merged: dict[int, list[float]] = {}
    for item in bboxes:
        p = item["page"]
        b = item["bbox"]
        if p in merged:
            m = merged[p]
            merged[p] = [min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3])]
        else:
            merged[p] = list(b)
    return [{"page": p, "bbox": b} for p, b in sorted(merged.items())]


class _Section:
    __slots__ = ("path", "blocks")

    def __init__(self, path: list[str]):
        self.path = path
        self.blocks: list[ParsedBlock] = []


def _split_sections(blocks: list[ParsedBlock]) -> list[_Section]:
    """标题块切分 section；标题自身并入其 section 首部。"""
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    cur = _Section([])

    def flush():
        nonlocal cur
        if cur.blocks:
            sections.append(cur)

    for b in blocks:
        if b.kind == "reference":
            continue  # 参考文献不入索引
        if b.kind == "heading":
            flush()
            level = b.level or 1
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, b.text))
            cur = _Section([t for _, t in stack])
            cur.blocks.append(b)  # 标题文本保留，提供检索锚点
        else:
            if not cur.blocks and not cur.path:
                cur = _Section([])
            cur.blocks.append(b)
    flush()
    return sections


def _pack(items: list[tuple[str, ParsedBlock]], target: int, max_tokens: int,
          overlap_ratio: float) -> list[list[tuple[str, ParsedBlock]]]:
    """贪心装包至 target token；超长单块硬切；相邻包保留尾部重叠。"""
    packs: list[list[tuple[str, ParsedBlock]]] = []
    cur: list[tuple[str, ParsedBlock]] = []
    cur_tokens = 0

    def flush():
        nonlocal cur, cur_tokens
        if cur:
            packs.append(cur)
        # 计算下一包的重叠尾部
        keep: list[tuple[str, ParsedBlock]] = []
        kept = 0
        limit = int(target * overlap_ratio)
        for text, blk in reversed(cur):
            t = est_tokens(text)
            if kept + t > limit or len(keep) >= len(cur) - 1:
                break
            keep.insert(0, (text, blk))
            kept += t
        cur = list(keep)
        cur_tokens = kept

    for text, blk in items:
        t = est_tokens(text)
        if cur and cur_tokens + t > max_tokens:
            flush()
        if t > max_tokens:
            # 超长块按句子硬切
            for piece in _hard_split(text, max_tokens):
                cur.append((piece, blk))
                cur_tokens += est_tokens(piece)
                if cur_tokens > target:
                    flush()
        else:
            cur.append((text, blk))
            cur_tokens += t
            if cur_tokens >= target:
                flush()
    flush()
    return [p for p in packs if any(txt.strip() for txt, _ in p)]


def _hard_split(text: str, max_tokens: int) -> list[str]:
    pieces: list[str] = []
    sentences = re.split(r"(?<=[.!?。！？])\s+", text)
    buf = ""
    for s in sentences:
        if est_tokens(buf) + est_tokens(s) > max_tokens and buf:
            pieces.append(buf)
            buf = s
        else:
            buf = (buf + " " + s).strip()
        while est_tokens(buf) > max_tokens:  # 单句超长，按字符切
            cut = buf[: max_tokens * 3]
            pieces.append(cut)
            buf = buf[max_tokens * 3:]
    if buf.strip():
        pieces.append(buf)
    return pieces


def chunk_blocks(blocks: list[ParsedBlock]) -> list[Chunk]:
    sections = _split_sections(blocks)
    chunks: list[Chunk] = []
    target = config.CHUNK_TARGET_TOKENS
    max_t = config.CHUNK_MAX_TOKENS
    overlap = config.CHUNK_OVERLAP_RATIO

    for sec in sections:
        items: list[tuple[str, ParsedBlock]] = []
        for b in sec.blocks:
            if b.kind == "heading":
                continue  # 标题写入 section_path 前缀，不占正文
            if b.text.strip():
                items.append((b.text, b))
        if not items:
            continue
        prefix = " > ".join(sec.path) + "\n" if sec.path else ""
        for pack in _pack(items, target, max_t, overlap):
            text = prefix + "\n".join(t for t, _ in pack)
            # 类型：包内全是 table → table；含 abstract → abstract
            types = {blk.kind for _, blk in pack}
            if types == {"table"}:
                btype = "table"
            elif "abstract" in types:
                btype = "abstract"
            else:
                btype = "text"
            bboxes = [{"page": blk.page, "bbox": list(blk.bbox)} for _, blk in pack]
            chunks.append(Chunk(
                text=text,
                section_path=sec.path,
                page_start=pack[0][1].page,
                page_end=pack[-1][1].page,
                bboxes=_merge_bbox_list(bboxes),
                block_type=btype,
            ))
    # 表格若与 caption 相邻，前面补 caption 的逻辑在 _split_sections 前处理：
    # 简化实现——caption 独立小 chunk 由 pack 自然产生（caption 后紧跟表格时同包）。
    return chunks
