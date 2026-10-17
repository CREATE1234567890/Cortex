"use client";

import React from "react";
import { Citation } from "@/lib/api";

/**
 * 轻量 Markdown 渲染（标题/列表/表格/代码/引用块/粗斜体），
 * 并把正文中的 [n] 渲染为可点击引用角标。
 * 刻意不引第三方 markdown 库：引用 chip 需要与流式渲染深度集成。
 */

interface Chunk {
  type: "h1" | "h2" | "h3" | "h4" | "p" | "ul" | "ol" | "blockquote" | "pre" | "table";
  lines: string[];
}

function splitBlocks(md: string): Chunk[] {
  const lines = md.split("\n");
  const chunks: Chunk[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (line.startsWith("```")) {
      const buf: string[] = [line];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        buf.push(lines[i]); i++;
      }
      if (i < lines.length) { buf.push(lines[i]); i++; }
      chunks.push({ type: "pre", lines: buf });
      continue;
    }
    if (line.startsWith("#### ")) chunks.push({ type: "h4", lines: [line.slice(5)] }), i++;
    else if (line.startsWith("### ")) chunks.push({ type: "h3", lines: [line.slice(4)] }), i++;
    else if (line.startsWith("## ")) chunks.push({ type: "h2", lines: [line.slice(3)] }), i++;
    else if (line.startsWith("# ")) chunks.push({ type: "h1", lines: [line.slice(2)] }), i++;
    else if (/^\s*[-*+]\s+/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*[-*+]\s+/, "")); i++;
      }
      chunks.push({ type: "ul", lines: buf });
    } else if (/^\s*\d+[.、)]\s+/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*\d+[.、)]\s+/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*\d+[.、)]\s+/, "")); i++;
      }
      chunks.push({ type: "ol", lines: buf });
    } else if (line.startsWith(">")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].startsWith(">")) {
        buf.push(lines[i].replace(/^>\s?/, "")); i++;
      }
      chunks.push({ type: "blockquote", lines: buf });
    } else if (line.trim().startsWith("|") && line.includes("|", 1)) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        buf.push(lines[i].trim()); i++;
      }
      chunks.push({ type: "table", lines: buf });
    } else if (line.trim() === "") {
      i++;
    } else {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim() !== "" &&
        !/^(#{1,4}\s|```|\s*[-*+]\s|\s*\d+[.、)]\s|>|\|)/.test(lines[i])) {
        buf.push(lines[i]); i++;
      }
      chunks.push({ type: "p", lines: buf });
    }
  }
  return chunks;
}

const INLINE_RE = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[\d{1,2}\]|\[W\d{1,2}\])/g;

/** 递归渲染行内元素：加粗/斜体内部的 [n] 引用同样转为可点击角标 */
function renderInline(
  text: string,
  citations: Map<string | number, Citation>,
  onCite: (c: Citation) => void,
  keyPrefix: string,
): React.ReactNode[] {
  const parts = text.split(INLINE_RE).filter(s => s !== "");
  return parts.map((part, idx) => {
    const key = `${keyPrefix}-${idx}`;
    if (/^\*\*([^*]+)\*\*$/.test(part)) {
      return <strong key={key}>{renderInline(part.slice(2, -2), citations, onCite, key + "b")}</strong>;
    }
    if (/^\*([^*]+)\*$/.test(part)) {
      return <em key={key}>{renderInline(part.slice(1, -1), citations, onCite, key + "i")}</em>;
    }
    if (/^`[^`]+`$/.test(part)) return <code key={key}>{part.slice(1, -1)}</code>;
    const m = part.match(/^\[(\d{1,2})\]$/);
    if (m) {
      const n = parseInt(m[1], 10);
      const cit = citations.get(n);
      if (!cit) return null; // 引用验证未兜底的编号不渲染（与后端验证结果保持一致）
      return (
        <span
          key={key}
          className="cite"
          title={`《${cit.title}》 p.${cit.page}${cit.section_path?.length ? " ｜ " + cit.section_path.join(" > ") : ""}`}
          onClick={e => { e.stopPropagation(); onCite(cit); }}
        >
          {n}
        </span>
      );
    }
    const wm = part.match(/^\[W(\d{1,2})\]$/);
    if (wm) {
      const cit = citations.get(`W${wm[1]}`);
      if (!cit) return null; // 未兜底的库外编号不渲染
      return (
        <span
          key={key}
          className="cite ext"
          title={`[库外来源] ${cit.title}${cit.source ? " ｜ " + cit.source : ""}`}
          onClick={e => { e.stopPropagation(); onCite(cit); }}
        >
          W{wm[1]}
        </span>
      );
    }
    return <React.Fragment key={key}>{part}</React.Fragment>;
});
}

export default function Markdown({
  text,
  citations,
  onCite,
}: {
  text: string;
  citations: Citation[];
  onCite: (c: Citation) => void;
}) {
  const citMap = new Map(citations.map(c => [c.n, c]));
  const blocks = splitBlocks(text);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        switch (b.type) {
          case "h1": return <h1 key={i}>{renderInline(b.lines[0], citMap, onCite, `h1${i}`)}</h1>;
          case "h2": return <h2 key={i}>{renderInline(b.lines[0], citMap, onCite, `h2${i}`)}</h2>;
          case "h3": return <h3 key={i}>{renderInline(b.lines[0], citMap, onCite, `h3${i}`)}</h3>;
          case "h4": return <h4 key={i}>{renderInline(b.lines[0], citMap, onCite, `h4${i}`)}</h4>;
          case "p": return <p key={i}>{renderInline(b.lines.join(" "), citMap, onCite, `p${i}`)}</p>;
          case "ul":
            return (
              <ul key={i}>
                {b.lines.map((li, j) => <li key={j}>{renderInline(li, citMap, onCite, `ul${i}-${j}`)}</li>)}
              </ul>
            );
          case "ol":
            return (
              <ol key={i}>
                {b.lines.map((li, j) => <li key={j}>{renderInline(li, citMap, onCite, `ol${i}-${j}`)}</li>)}
              </ol>
            );
          case "blockquote":
            return (
              <blockquote key={i}>
                {b.lines.map((li, j) => <p key={j}>{renderInline(li, citMap, onCite, `bq${i}-${j}`)}</p>)}
              </blockquote>
            );
          case "pre":
            return (
              <pre key={i}><code>{b.lines.slice(1, -1).join("\n")}</code></pre>
            );
          case "table": {
            const rows = b.lines
              .filter(l => !/^\|[\s:|-]+\|$/.test(l))
              .map(l => l.split("|").slice(1, -1).map(c => c.trim()));
            const [head, ...body] = rows;
            return (
              <table key={i}>
                {head && <thead><tr>{head.map((c, j) => <th key={j}>{renderInline(c, citMap, onCite, `th${i}-${j}`)}</th>)}</tr></thead>}
                <tbody>
                  {body.map((r, ri) => (
                    <tr key={ri}>{r.map((c, ci) => <td key={ci}>{renderInline(c, citMap, onCite, `td${i}-${ri}-${ci}`)}</td>)}</tr>
                  ))}
                </tbody>
              </table>
            );
          }
        }
      })}
    </div>
  );
}
