"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, Citation } from "@/lib/api";
import { ensureRafWorks } from "@/lib/raf";

export interface PdfTarget {
  docId: string;
  title: string;
  page: number;            // 1-based
  bboxes: Citation["bboxes"];
  token: number;           // 变化即使内容相同也重新定位
}

/**
 * PDF 阅读器（单页渲染版）：
 * - 只渲染当前页画布，prev/next/页码跳转；引用定位无竞态；
 * - 高亮在页面渲染完成后用同一 viewport 变换同步绘制（bbox 为 PDF 点坐标）；
 * - 加载前确保 rAF 可用（嵌入式浏览器降级，见 lib/raf.ts）。
 */
export default function PdfViewer({ target }: { target: PdfTarget | null }) {
  const holderRef = useRef<HTMLDivElement>(null);
  const [pdfDoc, setPdfDoc] = useState<any>(null);
  const [numPages, setNumPages] = useState(0);
  const [curPage, setCurPage] = useState(1);
  const [scale, setScale] = useState(1.5);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const highlightsRef = useRef<Citation["bboxes"]>([]);
  const renderSeq = useRef(0);

  // 加载文档
  useEffect(() => {
    if (!target) return;
    let cancelled = false;
    setErr("");
    setPdfDoc(null);
    setNumPages(0);
    setLoading(true);
    (async () => {
      try {
        await ensureRafWorks();
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
        const doc = await pdfjs.getDocument({ url: api.pdfUrl(target.docId) }).promise;
        if (cancelled) { doc.destroy(); return; }
        setPdfDoc(doc);
        setNumPages(doc.numPages);
      } catch (e) {
        if (!cancelled) setErr(`PDF 加载失败：${e}`);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [target?.docId]);

  // 引用定位
  useEffect(() => {
    if (!target || !pdfDoc) return;
    highlightsRef.current = target.bboxes || [];
    const p = Math.max(1, Math.min(numPages, target.page));
    setCurPage(p);
  }, [target?.token, pdfDoc, numPages]);

  // 渲染当前页 + 高亮
  const renderPage = useCallback(async (pageNo: number) => {
    if (!pdfDoc) return;
    const seq = ++renderSeq.current;
    const holder = holderRef.current;
    if (!holder) return;
    try {
      const page = await pdfDoc.getPage(pageNo);
      if (seq !== renderSeq.current) return;
      const dpr = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: scale * dpr });
      holder.innerHTML = "";
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width / dpr}px`;
      canvas.style.height = `${viewport.height / dpr}px`;
      holder.style.width = `${viewport.width / dpr}px`;
      holder.style.height = `${viewport.height / dpr}px`;
      await page.render({ canvasContext: canvas.getContext("2d")!, viewport }).promise;
      if (seq !== renderSeq.current) return;
      holder.appendChild(canvas);
      // 高亮层：与 canvas 同一 viewport 变换
      for (const b of highlightsRef.current) {
        if (b.page !== pageNo - 1) continue;
        const [x0, y0] = viewport.convertToViewportPoint(b.bbox[0], b.bbox[1]);
        const [x1, y1] = viewport.convertToViewportPoint(b.bbox[2], b.bbox[3]);
        const div = document.createElement("div");
        div.className = "pdf-highlight";
        div.style.left = `${Math.min(x0, x1) / dpr}px`;
        div.style.top = `${Math.min(y0, y1) / dpr}px`;
        div.style.width = `${Math.abs(x1 - x0) / dpr}px`;
        div.style.height = `${Math.abs(y1 - y0) / dpr}px`;
        holder.appendChild(div);
      }
    } catch (e) {
      if (seq === renderSeq.current) setErr(`页面渲染失败：${e}`);
    }
  }, [pdfDoc, scale]);

  useEffect(() => { renderPage(curPage); }, [renderPage, curPage]);

  const goPage = (p: number) => {
    if (!pdfDoc) return;
    setCurPage(Math.max(1, Math.min(numPages, p)));
  };

  // 键盘翻页：←/→ 或 PageUp/PageDown（面板获得焦点时生效）
  const onPanelKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); goPage(curPage - 1); }
    else if (e.key === "ArrowRight" || e.key === "PageDown") { e.preventDefault(); goPage(curPage + 1); }
  };

  if (!target) {
    return (
      <div className="pdf-panel" tabIndex={0} onKeyDown={onPanelKeyDown}>
        <div className="pdf-empty">
          <div style={{ fontSize: 34 }}>📄</div>
          <div>点击回答中的引用角标<br />这里会打开原文对应页并高亮证据区域</div>
        </div>
      </div>
    );
  }

  return (
    <div className="pdf-panel" tabIndex={0} onKeyDown={onPanelKeyDown}>
      <div className="pdf-toolbar">
        <span className="doc-title" title={target.title}>《{target.title}》</span>
        <button className="small" onClick={() => goPage(curPage - 1)} disabled={curPage <= 1}>‹</button>
        <span>第 {curPage} / {numPages} 页</span>
        <button className="small" onClick={() => goPage(curPage + 1)} disabled={curPage >= numPages}>›</button>
        <span style={{ flex: 1 }} />
        <button className="small" onClick={() => setScale(s => Math.max(0.6, s - 0.25))}>－</button>
        <span>{Math.round(scale * 100)}%</span>
        <button className="small" onClick={() => setScale(s => Math.min(3, s + 0.25))}>＋</button>
      </div>
      <div className="pdf-scroll">
        {err && <div style={{ color: "#ffb4b0", padding: 20 }}>{err}</div>}
        {loading && <div style={{ padding: 30, color: "#ddd" }}><span className="spin" /> 正在打开 PDF…</div>}
        <div className="pdf-page-wrap" ref={holderRef} />
      </div>
    </div>
  );
}
