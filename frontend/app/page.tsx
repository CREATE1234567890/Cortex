"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, Citation, ConversationInfo, EvidenceInfo, ToolInfo } from "@/lib/api";
import { useLang } from "@/components/Lang";
import Markdown from "@/components/Markdown";
import PdfViewer, { PdfTarget } from "@/components/PdfViewer";

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  evidences: EvidenceInfo[];
  tools: { query: string; added: number | null; web?: boolean }[];
  refused?: boolean;
  streaming?: boolean;
  phase?: "searching" | "generating";
  stopped?: boolean;
}

/** 可折叠检索过程块：流式期间自动展开，完成后默认折叠（ZCode/Claude 式） */
function ProcessBlock({ m, onOpenEvidence }: { m: ChatMsg; onOpenEvidence: (ev: EvidenceInfo) => void }) {
  const { t } = useLang();
  const [override, setOverride] = useState<boolean | null>(null);
  const open = override ?? (m.streaming ? true : false);
  const running = m.streaming && (!m.content || !!m.phase);
  const toolCount = m.tools.length;
  const title = m.phase === "searching" && m.streaming
    ? t("chat_searching")
    : m.streaming
      ? (toolCount ? t("chat_generating_tools", { n: m.evidences.length, m: toolCount }) : t("chat_generating", { n: m.evidences.length }))
      : (toolCount ? t("chat_process_done_tools", { n: m.evidences.length, m: toolCount }) : t("chat_process_done", { n: m.evidences.length }));

  if (!m.streaming && !m.evidences.length && !toolCount) return null;

  return (
    <div className={`process-block ${running ? "running" : ""}`}>
      <button className="process-head" onClick={() => setOverride(!open)}>
        <span className={`chev ${open ? "open" : ""}`}>▸</span>
        <span className="process-title">
          {running && <span className="spin" style={{ marginRight: 6 }} />}
          {title}
        </span>
      </button>
      {open && (
        <div className="process-body">
          {m.tools.map((x, j) => (
            <div key={j} className="process-line">
              {x.web ? "🌐" : "🔍"}{" "}
              {x.added === null
                ? (x.web ? `在线检索「${x.query}」…` : t("chat_tool_pending", { q: x.query }))
                : (x.web
                    ? `在线检索「${x.query}」＋${x.added} 条库外文献`
                    : t("chat_tool_done", { q: x.query }) + t("chat_tool_added", { n: x.added }))}
            </div>
          ))}
          {m.evidences.length > 0 && (
            <div className="evidence-strip">
              {m.evidences.map(ev => (
                <EvChip key={ev.chunk_id} ev={ev} onOpen={onOpenEvidence} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EvChip({ ev, onOpen }: { ev: EvidenceInfo; onOpen: (ev: EvidenceInfo) => void }) {
  return (
    <span className="ev-chip" onClick={() => onOpen(ev)} title={ev.snippet}>
      <span className="n">{ev.n}</span>
      <span className="t">《{ev.title}》 p.{ev.page}</span>
    </span>
  );
}

export default function ChatPage() {
  const { t, lang } = useLang();
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [convId, setConvId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [convSearch, setConvSearch] = useState("");
  const [docs, setDocs] = useState<{ id: string; title: string; status: string }[]>([]);
  const [scopeIds, setScopeIds] = useState<string[]>([]);
  const [mention, setMention] = useState<{ query: string; start: number } | null>(null);
  const [mentionIdx, setMentionIdx] = useState(0);
  const [pdfTarget, setPdfTarget] = useState<PdfTarget | null>(null);
  const [pdfOpen, setPdfOpen] = useState(false);
  const [copiedIdx, setCopiedIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const nearBottomRef = useRef(true);
  const abortRef = useRef<AbortController | null>(null);
  const textRef = useRef("");
  const flushTimer = useRef<number | null>(null);
  const lastQuestionRef = useRef("");

  const suggestions = [t("sugg_1"), t("sugg_2"), t("sugg_3"), t("sugg_4")];

  const refreshLists = useCallback(() => {
    api.conversations().then(setConversations).catch(() => {});
    api.documents().then(all => setDocs(all.map(d => ({ id: d.id, title: d.title, status: d.status })))).catch(() => {});
  }, []);

  useEffect(refreshLists, [refreshLists]);

  const indexedDocs = docs.filter(d => d.status === "indexed");
  const scopeDocs = scopeIds
    .map(id => indexedDocs.find(d => d.id === id))
    .filter(Boolean) as { id: string; title: string }[];

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    nearBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 140;
  };

  const scrollBottom = (force = false) => {
    if (!force && !nearBottomRef.current) return;
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight }));
  };

  const flushNow = () => {
    if (flushTimer.current !== null) {
      window.clearTimeout(flushTimer.current);
      flushTimer.current = null;
    }
    setMessages(m => {
      if (!m.length || m[m.length - 1].role !== "assistant") return m;
      const copy = [...m];
      copy[copy.length - 1] = { ...copy[copy.length - 1], content: textRef.current };
      return copy;
    });
  };

  function autoGrow() {
    const el = taRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }

  // @ 提及检测：光标前最近的 @词（value 缺省时读输入框实时值，避免闭包旧 state）
  function detectMention(value?: string) {
    const el = taRef.current;
    if (!el) { setMention(null); return; }
    const text = value ?? el.value;
    const caret = el.selectionStart ?? text.length;
    const before = text.slice(0, caret);
    const m = before.match(/@([^\s@]*)$/);
    if (m) setMention({ query: m[1], start: caret - m[1].length - 1 });
    else setMention(null);
    setMentionIdx(0);
  }

  const mentionCandidates = mention
    ? indexedDocs
      .filter(d => !scopeIds.includes(d.id))
      .filter(d => !mention.query || d.title.toLowerCase().includes(mention.query.toLowerCase()))
      .slice(0, 6)
    : [];

  function pickMention(doc: { id: string; title: string }) {
    if (!mention) return;
    const el = taRef.current;
    const caret = el?.selectionStart ?? input.length;
    const next = (input.slice(0, mention.start) + input.slice(caret)).replace(/\s{2,}/g, " ");
    setInput(next);
    setScopeIds(ids => (ids.includes(doc.id) ? ids : [...ids, doc.id]));
    setMention(null);
    requestAnimationFrame(() => { taRef.current?.focus(); autoGrow(); });
  }

  async function send(questionRaw?: string) {
    const question = (questionRaw ?? input).trim();
    if (!question || busy) return;
    setInput("");
    requestAnimationFrame(autoGrow);
    setBusy(true);
    lastQuestionRef.current = question;
    nearBottomRef.current = true;
    textRef.current = "";
    setMessages(m => [...m, {
      role: "user", content: question, citations: [], evidences: [], tools: [],
    }, {
      role: "assistant", content: "", citations: [], evidences: [], tools: [], streaming: true, phase: "searching",
    }]);
    scrollBottom(true);

    const patch = (fn: (m: ChatMsg) => ChatMsg) =>
      setMessages(m => {
        if (!m.length || m[m.length - 1].role !== "assistant") return m;
        const copy = [...m];
        copy[copy.length - 1] = fn(copy[copy.length - 1]);
        return copy;
      });

    const ctrl = new AbortController();
    abortRef.current = ctrl;

    try {
      await api.chat(question, convId, scopeIds.length ? scopeIds : null, {
        onMeta: id => { setConvId(id); refreshLists(); },
        onEvidence: (evidences) => {
          patch(m => ({ ...m, evidences, phase: evidences.length ? "generating" : m.phase }));
          scrollBottom();
        },
        onTool: (info: ToolInfo & { phase: string; name?: string; evidences?: EvidenceInfo[]; total?: number }) => {
          patch(m => {
            if (info.phase === "start") {
              return { ...m, phase: "generating", tools: [...m.tools, { query: info.query, added: null, web: info.name === "search_web" }] };
            }
            const tools = m.tools.slice();
            for (let i = tools.length - 1; i >= 0; i--) {
              if (tools[i].query === info.query && tools[i].added === null) {
                tools[i] = { ...tools[i], added: info.added ?? 0, web: info.name === "search_web" };
                break;
              }
            }
            const existing = new Set(m.evidences.map(e => e.chunk_id));
            const merged = [...m.evidences, ...(info.evidences || []).filter(e => !existing.has(e.chunk_id))];
            return { ...m, tools, evidences: merged };
          });
          scrollBottom();
        },
        onDelta: text => {
          textRef.current += text;
          if (flushTimer.current === null) {
            flushTimer.current = window.setTimeout(() => {
              flushTimer.current = null;
              flushNow();
              scrollBottom();
            }, 80);
          }
        },
        onCitations: citations => { patch(m => ({ ...m, citations })); },
        onDone: info => {
          flushNow();
          patch(m => ({ ...m, streaming: false, refused: info.refused, phase: undefined }));
          refreshLists();
        },
        onError: message => {
          flushNow();
          patch(m => ({ ...m, streaming: false, content: (m.content || "") + (m.content ? "\n\n" : "") + `⚠️ ${message}` }));
        },
      }, ctrl.signal);
    } catch (e) {
      flushNow();
      if ((e as Error).name === "AbortError") {
        patch(m => ({ ...m, stopped: true, streaming: false, content: m.content || "…" }));
      } else {
        patch(m => ({ ...m, streaming: false, content: (m.content || "") + (m.content ? "\n\n" : "") + `⚠️ 连接失败：${e}` }));
      }
    } finally {
      flushNow();
      abortRef.current = null;
      setBusy(false);
      patch(m => ({ ...m, streaming: false, phase: undefined }));
    }
  }

  function stop() {
    abortRef.current?.abort();
  }

  function regenerate() {
    if (busy || !lastQuestionRef.current) return;
    send(lastQuestionRef.current);
  }

  function copyMessage(i: number) {
    const m = messages[i];
    if (!m) return;
    navigator.clipboard.writeText(m.content).then(() => {
      setCopiedIdx(i);
      setTimeout(() => setCopiedIdx(null), 1600);
    }).catch(() => {});
  }

  function openCitation(c: Citation) {
    if (c.external) {
      if (c.url) window.open(c.url, "_blank", "noopener");
      return;
    }
    setPdfTarget({
      docId: c.doc_id, title: c.title, page: c.page,
      bboxes: c.bboxes || [], token: Date.now(),
    });
    setPdfOpen(true);
  }

  function openEvidence(ev: EvidenceInfo) {
    setPdfTarget({
      docId: ev.doc_id, title: ev.title, page: ev.page,
      bboxes: ev.bboxes || [], token: Date.now(),
    });
    setPdfOpen(true);
  }

  async function loadConversation(id: string) {
    if (busy) return;
    const msgs = await api.messages(id);
    setConvId(id);
    setMessages(msgs.map(m => ({
      role: m.role as "user" | "assistant",
      content: m.content,
      citations: m.citations || [],
      evidences: [], tools: [],
    })));
    const lastUser = [...msgs].reverse().find(x => x.role === "user");
    lastQuestionRef.current = lastUser?.content || "";
    nearBottomRef.current = true;
    scrollBottom(true);
  }

  function newChat() {
    setConvId(null);
    setMessages([]);
    setPdfTarget(null);
    lastQuestionRef.current = "";
  }

  async function deleteConv(id: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm(t("chat_del_conv"))) return;
    await api.deleteConversation(id);
    if (id === convId) newChat();
    refreshLists();
  }

  // 会话分组：今天 / 7 天内 / 更早（叠加标题搜索）
  const filteredConvs = conversations.filter(c =>
    !convSearch || (c.title || "").toLowerCase().includes(convSearch.toLowerCase()));
  const groupOf = (c: ConversationInfo) => {
    const d = new Date((c.created_at || "").replace(" ", "T"));
    const days = (Date.now() - d.getTime()) / 86400000;
    if (days < 1 && new Date().getDate() === d.getDate()) return t("chat_group_today");
    if (days < 7) return t("chat_group_week");
    return t("chat_group_earlier");
  };
  const groups: [string, ConversationInfo[]][] = (() => {
    const map = new Map<string, ConversationInfo[]>();
    for (const c of filteredConvs) {
      const g = groupOf(c);
      if (!map.has(g)) map.set(g, []);
      map.get(g)!.push(c);
    }
    return [...map.entries()];
  })();

  return (
    <div className="page">
      <div className="chat-layout">
        {/* 会话侧栏 */}
        <aside className="conv-sidebar">
          <div style={{ padding: "12px 14px 8px" }}>
            <button className="primary" style={{ width: "100%" }} onClick={newChat}>{t("chat_new")}</button>
          </div>
          <div style={{ padding: "0 14px 10px" }}>
            <input
              type="text"
              placeholder={t("chat_search_conv")}
              value={convSearch}
              onChange={e => setConvSearch(e.target.value)}
            />
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "0 8px 8px" }}>
            {groups.map(([label, items]) => (
              <div key={label}>
                <div className="conv-group-label">{label}</div>
                {items.map(c => (
                  <div
                    key={c.id}
                    className={`conv-item ${c.id === convId ? "active" : ""}`}
                    onClick={() => loadConversation(c.id)}
                    title={c.title}
                  >
                    <span className="conv-title">{c.title || t("chat_new").replace("＋ ", "")}</span>
                    <span className="conv-time">{(c.created_at || "").slice(5, 16).replace("T", " ")}</span>
                    <button className="conv-del" onClick={e => deleteConv(c.id, e)} title={t("lib_delete")}>✕</button>
                  </div>
                ))}
              </div>
            ))}
            {!filteredConvs.length && (
              <div style={{ fontSize: 12, color: "var(--text-3)", padding: "10px 6px" }}>{t("chat_no_conv")}</div>
            )}
          </div>
        </aside>

        {/* 聊天主区 */}
        <div className="chat-main">
          <div className="chat-scroll" ref={scrollRef} onScroll={onScroll}>
          <div className="chat-inner">
            {messages.length === 0 && (
              <div className="empty-state">
                <div className="big">🧠</div>
                <h3 style={{ color: "var(--text-2)", margin: "0 0 8px" }}>{t("chat_empty_title")}</h3>
                <div>{t("chat_empty_sub")}</div>
                <div style={{ marginTop: 10, fontSize: 12.5 }}>
                  <span dangerouslySetInnerHTML={{
                    __html: t("chat_empty_at").replace("@", '<code class="kbd">@</code>'),
                  }} />
                </div>
                <div style={{ marginTop: 18, display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "center" }}>
                  {suggestions.map(q => (
                    <button key={q} className="small" onClick={() => send(q)}>{q}</button>
                  ))}
                </div>
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.role}`}>
                {m.role === "assistant" && <div className="role">{t("role_cortex")}</div>}

                {m.role === "assistant" && (
                  <ProcessBlock m={m} onOpenEvidence={openEvidence} />
                )}

                <div className={`bubble ${m.role === "assistant" && m.refused ? "refused" : ""}`}>
                  {m.role === "user"
                    ? <span style={{ whiteSpace: "pre-wrap" }}>{m.content}</span>
                    : (m.content
                      ? <Markdown text={m.content} citations={m.citations} onCite={openCitation} />
                      : (m.streaming && <span className="thinking"><span className="pulse">●</span> {t("chat_thinking")}</span>))}
                  {m.streaming && m.content && <span className="thinking pulse">▍</span>}
                  {m.stopped && <div className="stopped-note">{t("chat_stopped")}</div>}
                </div>

                {/* 悬浮操作栏 */}
                {m.role === "assistant" && !m.streaming && m.content && (
                  <div className="msg-actions">
                    <button className="action-btn" onClick={() => copyMessage(i)} title="Markdown">
                      {copiedIdx === i ? t("chat_copied") : t("chat_copy")}
                    </button>
                    <button className="action-btn" onClick={regenerate} disabled={busy} title={t("chat_reask")}>
                      {t("chat_reask")}
                    </button>
                    {m.citations.length > 0 && (
                      <span className="cite-verified" title={t("chat_verified", { n: m.citations.length })}>
                        ✓ {t("chat_verified", { n: m.citations.length }).replace("✓ ", "")}
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
          </div>
          <div className="chat-input-area">
            {scopeDocs.length > 0 && (
              <div className="scope-chips">
                <span className="scope-hint">{t("chat_scope_hint")}</span>
                {scopeDocs.map(d => (
                  <span key={d.id} className="scope-chip" title={d.title}>
                    《{d.title.slice(0, 28)}》{d.title.length > 28 ? "…" : ""}
                    <button onClick={() => setScopeIds(ids => ids.filter(x => x !== d.id))} title={t("chat_scope_del")}>✕</button>
                  </span>
                ))}
                <button className="scope-clear" onClick={() => setScopeIds([])}>{t("chat_scope_clear")}</button>
              </div>
            )}
            <div className="chat-input-box">
              <button
                className="composer-add"
                title={t("chat_mention_head")}
                onClick={() => {
                  const el = taRef.current;
                  if (!el) return;
                  const v = el.value;
                  const caret = el.selectionStart ?? v.length;
                  const left = v.slice(0, caret).replace(/\s+$/, "");
                  const right = v.slice(caret);
                  const nextV = `${left} @${right}`;
                  setInput(nextV);
                  const caretPos = left.length + 2;
                  requestAnimationFrame(() => {
                    const el2 = taRef.current;
                    if (el2) { el2.focus(); el2.selectionStart = el2.selectionEnd = caretPos; }
                    detectMention(nextV.slice(0, caretPos));
                  });
                }}
              >＋</button>
              <textarea
                ref={taRef}
                rows={1}
                placeholder={t("chat_input_ph")}
                value={input}
                onChange={e => { setInput(e.target.value); autoGrow(); detectMention(e.target.value); }}
                onClick={() => detectMention()}
                onKeyUp={() => detectMention()}
                onKeyDown={e => {
                  if (mention && mentionCandidates.length) {
                    if (e.key === "ArrowDown") { e.preventDefault(); setMentionIdx(i => (i + 1) % mentionCandidates.length); return; }
                    if (e.key === "ArrowUp") { e.preventDefault(); setMentionIdx(i => (i - 1 + mentionCandidates.length) % mentionCandidates.length); return; }
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                      e.preventDefault(); pickMention(mentionCandidates[mentionIdx]); return;
                    }
                    if (e.key === "Escape") { setMention(null); return; }
                  }
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    send();
                  }
                }}
                autoFocus
              />
              {busy ? (
                <button className="stop-btn" onClick={stop} title={t("chat_stop")}>■</button>
              ) : (
                <button className="primary send-btn" onClick={() => send()} disabled={!input.trim()}
                        title={t("chat_send")}>↑</button>
              )}
              {mention && mentionCandidates.length > 0 && (
                <div className="mention-dropdown">
                  <div className="mention-head">{t("chat_mention_head")}</div>
                  {mentionCandidates.map((d, i) => (
                    <div
                      key={d.id}
                      className={`mention-item ${i === mentionIdx ? "hl" : ""}`}
                      onMouseEnter={() => setMentionIdx(i)}
                      onClick={() => pickMention(d)}
                    >
                      《{d.title}》
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

        {/* PDF 原文面板：右侧常驻窄条控制开合 */}
        {pdfOpen && <PdfViewer target={pdfTarget} />}
        <div
          className="pdf-rail"
          onClick={() => setPdfOpen(o => !o)}
          title={pdfOpen ? t("chat_pdf_open") : t("chat_pdf_closed")}
        >
          <span className="pdf-rail-icon">{pdfOpen ? "»" : "«"}</span>
          <span className="pdf-rail-doc">📄</span>
        </div>
      </div>
    </div>
  );
}
