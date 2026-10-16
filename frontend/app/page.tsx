"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, Citation, ConversationInfo, EvidenceInfo } from "@/lib/api";
import Markdown from "@/components/Markdown";
import PdfViewer, { PdfTarget } from "@/components/PdfViewer";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  evidences: EvidenceInfo[];
  refused?: boolean;
  streaming?: boolean;
  phase?: "searching" | "generating";
}

export default function ChatPage() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [convId, setConvId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [docFilter, setDocFilter] = useState<string[]>([]);
  const [docs, setDocs] = useState<{ id: string; title: string; status: string }[]>([]);
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null);
  const [pdfOpen, setPdfOpen] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  const refreshLists = useCallback(() => {
    api.conversations().then(setConversations).catch(() => {});
    api.documents().then(all => setDocs(all.map(d => ({ id: d.id, title: d.title, status: d.status })))).catch(() => {});
  }, []);

  useEffect(refreshLists, [refreshLists]);

  const scrollBottom = () => {
    requestAnimationFrame(() => {
      scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    });
  };

  async function send() {
    const question = input.trim();
    if (!question || busy) return;
    setInput("");
    setBusy(true);
    setMessages(m => [...m, {
      role: "user", content: question, citations: [], evidences: [],
    }, {
      role: "assistant", content: "", citations: [], evidences: [], streaming: true, phase: "searching",
    }]);
    scrollBottom();

    let assistantText = "";
    const patch = (fn: (m: ChatMsg) => ChatMsg) =>
      setMessages(m => {
        const copy = [...m];
        copy[copy.length - 1] = fn(copy[copy.length - 1]);
        return copy;
      });

    try {
      await api.chat(question, convId, docFilter.length ? docFilter : null, {
        onMeta: id => { setConvId(id); refreshLists(); },
        onEvidence: (evidences) => {
          patch(m => ({ ...m, evidences, phase: "generating" }));
          scrollBottom();
        },
        onDelta: text => {
          assistantText += text;
          patch(m => ({ ...m, content: assistantText }));
          scrollBottom();
        },
        onCitations: citations => {
          patch(m => ({ ...m, citations }));
        },
        onDone: info => {
          patch(m => ({ ...m, streaming: false, refused: info.refused, phase: undefined }));
        },
        onError: message => {
          patch(m => ({ ...m, streaming: false, content: (m.content || "") + `\n\n⚠️ ${message}` }));
        },
      });
    } catch (e) {
      patch(m => ({ ...m, streaming: false, content: (m.content || "") + `\n\n⚠️ 连接失败：${e}` }));
    } finally {
      setBusy(false);
      patch(m => ({ ...m, streaming: false }));
    }
  }

  function openCitation(c: Citation) {
    setPdfTarget({
      docId: c.doc_id,
      title: c.title,
      page: c.page,
      bboxes: c.bboxes || [],
      token: Date.now(),
    });
    setPdfOpen(true);
  }

  function openEvidence(ev: EvidenceInfo) {
    setPdfTarget({
      docId: ev.doc_id,
      title: ev.title,
      page: ev.page,
      bboxes: ev.bboxes || [],
      token: Date.now(),
    });
    setPdfOpen(true);
  }

  async function loadConversation(id: string) {
    const msgs = await api.messages(id);
    setConvId(id);
    setMessages(msgs.map(m => ({
      role: m.role as "user" | "assistant",
      content: m.content,
      citations: m.citations || [],
      evidences: [],
    })));
    scrollBottom();
  }

  function newChat() {
    setConvId(null);
    setMessages([]);
    setPdfTarget(null);
  }

  async function deleteConv(id: string) {
    await api.deleteConversation(id);
    if (id === convId) newChat();
    refreshLists();
  }

  const indexedDocs = docs.filter(d => d.status === "indexed");

  return (
    <div className="page">
      <div className="chat-layout">
        {/* 会话侧栏 */}
        <aside style={{
          width: 230, borderRight: "1px solid var(--border)", background: "var(--panel)",
          display: "flex", flexDirection: "column", minHeight: 0,
        }}>
          <div style={{ padding: "12px 14px 8px" }}>
            <button className="primary" style={{ width: "100%" }} onClick={newChat}>＋ 新对话</button>
          </div>
          {indexedDocs.length > 0 && (
            <div style={{ padding: "4px 14px 10px", borderBottom: "1px solid var(--border)" }}>
              <div style={{ fontSize: 12, color: "var(--text-3)", marginBottom: 6 }}>
                提问范围（默认全部 {indexedDocs.length} 篇）
              </div>
              <select
                multiple={false}
                value={docFilter.length === 1 ? docFilter[0] : ""}
                onChange={e => setDocFilter(e.target.value ? [e.target.value] : [])}
              >
                <option value="">全部文献</option>
                {indexedDocs.map(d => <option key={d.id} value={d.id}>{d.title.slice(0, 40)}</option>)}
              </select>
            </div>
          )}
          <div style={{ flex: 1, overflowY: "auto", padding: "8px" }}>
            {conversations.map(c => (
              <div
                key={c.id}
                onClick={() => loadConversation(c.id)}
                style={{
                  display: "flex", alignItems: "center", gap: 6, padding: "7px 10px",
                  borderRadius: 8, cursor: "pointer", fontSize: 13,
                  background: c.id === convId ? "var(--accent-soft)" : "transparent",
                  color: c.id === convId ? "var(--accent-deep)" : "var(--text-2)",
                }}
                onMouseEnter={e => { if (c.id !== convId) e.currentTarget.style.background = "#f0f2f5"; }}
                onMouseLeave={e => { if (c.id !== convId) e.currentTarget.style.background = "transparent"; }}
              >
                <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.title || "新对话"}
                </span>
                <button
                  className="small danger"
                  style={{ border: "none", background: "transparent", padding: "2px 5px" }}
                  onClick={e => { e.stopPropagation(); deleteConv(c.id); }}
                  title="删除"
                >✕</button>
              </div>
            ))}
          </div>
        </aside>

        {/* 聊天主区 */}
        <div className="chat-main">
          <div className="chat-scroll" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="empty-state">
                <div className="big">🧠</div>
                <h3 style={{ color: "var(--text-2)", margin: "0 0 8px" }}>问一个文献库里的问题</h3>
                <div>每个论断都带页码级引用，点击角标即可回到原文核对。</div>
                <div style={{ marginTop: 18, display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
                  {["Swin-Unet 的编码器是怎么设计的？",
                    "U2-Net 用于什么任务，效果如何？",
                    "库里的论文用了哪些医学图像分割数据集？",
                    "VMamba 相比 ViT 的优势是什么？"].map(q => (
                    <button key={q} className="small" onClick={() => { setInput(q); }}>{q}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                <div className="role">{m.role === "user" ? "你" : "Cortex"}</div>
                {m.role === "assistant" && m.evidences.length > 0 && (
                  <div className="evidence-strip">
                    {m.evidences.map(ev => (
                      <span key={ev.n} className="ev-chip" onClick={() => openEvidence(ev)} title={ev.snippet}>
                        <span className="n">{ev.n}</span>
                        <span className="t">《{ev.title}》 p.{ev.page}</span>
                      </span>
                    ))}
                  </div>
                )}
                <div className={`bubble ${m.role === "assistant" && m.refused ? "refused" : ""}`}>
                  {m.streaming && !m.content && m.phase === "searching" && (
                    <span className="thinking"><span className="pulse">●</span> 正在检索文献库…</span>
                  )}
                  {m.streaming && !m.content && m.phase === "generating" && (
                    <span className="thinking"><span className="pulse">●</span> 已找到 {m.evidences.length} 个证据块，正在生成带引用的回答…</span>
                  )}
                  {m.content && (
                    <Markdown text={m.content} citations={m.citations} onCite={openCitation} />
                  )}
                  {m.streaming && m.content && <span className="thinking pulse">▍</span>}
                </div>
              </div>
            ))}
          </div>
          <div className="chat-input-area">
            <div className="chat-input-box">
              <textarea
                rows={1}
                placeholder="向文献库提问…（Enter 发送，Shift+Enter 换行）"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send();
                  }
                }}
                autoFocus
              />
              <button className="primary" onClick={send} disabled={busy || !input.trim()}>
                {busy ? "回答中…" : "发送"}
              </button>
            </div>
          </div>
        </div>

        {/* PDF 阅读器 */}
        {pdfOpen && (
          <PdfViewer target={pdfTarget} />
        )}
      </div>
    </div>
  );
}
