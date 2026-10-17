const API = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8300";

export interface Citation {
  n: number;
  chunk_id: number;
  doc_id: string;
  title: string;
  page: number;
  page_end: number;
  section_path: string[];
  snippet: string;
  bboxes: { page: number; bbox: [number, number, number, number] }[];
  block_type: string;
}

export interface EvidenceInfo {
  n: number;
  chunk_id: number;
  doc_id: string;
  title: string;
  page: number;
  page_end: number;
  section_path: string[];
  snippet: string;
  bboxes: Citation["bboxes"];
  score: number;
}

export interface DocumentInfo {
  id: string;
  title: string;
  filename: string;
  year: number | null;
  venue: string | null;
  category: string | null;
  status: string;
  n_pages: number;
  n_chunks: number;
  error: string | null;
  created_at: string;
  indexed_at: string | null;
}

export interface ConversationInfo {
  id: string;
  title: string;
  created_at: string;
  n_messages: number;
}

export interface IngestJob {
  state: "idle" | "running" | "done" | "error";
  total: number;
  done: number;
  failed: number;
  current_file: string | null;
  message: string | null;
}

export interface ToolInfo {
  query: string;
  added: number | null;   // null = start 阶段
}

async function j<T>(resOrPromise: Response | Promise<Response>): Promise<T> {
  const res = await resOrPromise;
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || JSON.stringify(body);
    } catch { /* ignore */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => j<{ status: string; llm_ready: boolean }>(fetch(`${API}/api/health`)),
  documents: () => j<DocumentInfo[]>(fetch(`${API}/api/documents`)),
  conversations: () => j<ConversationInfo[]>(fetch(`${API}/api/conversations`)),
  messages: (convId: string) =>
    j<{ id: number; role: string; content: string; citations: Citation[] }[]>(
      fetch(`${API}/api/conversations/${convId}/messages`)),
  newConversation: () => j<{ id: string }>(
    fetch(`${API}/api/conversations`, { method: "POST" })),
  deleteConversation: (id: string) =>
    fetch(`${API}/api/conversations/${id}`, { method: "DELETE" }),
  deleteDocument: (id: string) =>
    fetch(`${API}/api/documents/${id}`, { method: "DELETE" }),
  retryDocument: (id: string) =>
    fetch(`${API}/api/documents/${id}/retry`, { method: "POST" }),
  scan: (root: string) =>
    j<{ root: string; total: number; files: { path: string; filename: string; size_mb: number; known: boolean }[] }>(
      fetch(`${API}/api/library/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root }),
      })),
  ingest: (paths: string[], incremental = true) =>
    j<{ started: boolean; queued?: number; reason?: string }>(
      fetch(`${API}/api/library/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths, incremental }),
      })),
  job: () => j<IngestJob>(fetch(`${API}/api/library/job`)),
  cancelJob: () => fetch(`${API}/api/library/job/cancel`, { method: "POST" }),
  settings: () =>
    j<Record<string, string | boolean>>(fetch(`${API}/api/settings`)),
  saveSettings: (patch: Record<string, string>) =>
    j<Record<string, string | boolean>>(fetch(`${API}/api/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    })),
  testLlm: () =>
    j<{ ok: boolean; reply?: string; error?: string }>(
      fetch(`${API}/api/settings/test-llm`, { method: "POST" })),
  pdfUrl: (docId: string) => `${API}/api/documents/${docId}/pdf`,

  /** 长期记忆 */
  memories: () =>
    j<{ enabled: boolean; items: { id: number; kind: string; content: string; created_at: string }[] }>(
      fetch(`${API}/api/memories`)),
  deleteMemory: (id: number) =>
    fetch(`${API}/api/memories/${id}`, { method: "DELETE" }),
  clearMemories: () =>
    fetch(`${API}/api/memories/clear`, { method: "POST" }),
  toggleMemory: (enabled: boolean) =>
    fetch(`${API}/api/memories/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    }),

  /** SSE 流式问答；onEvent 按 event 类型回调；signal 用于停止生成 */
  chat(
    question: string,
    conversationId: string | null,
    docIds: string[] | null,
    handlers: {
      onMeta?: (conversationId: string) => void;
      onEvidence?: (evidences: EvidenceInfo[], llmReady: boolean) => void;
      onTool?: (info: ToolInfo & { phase: string; evidences?: EvidenceInfo[]; total?: number }) => void;
      onDelta?: (text: string) => void;
      onCitations?: (citations: Citation[]) => void;
      onDone?: (info: { refused: boolean }) => void;
      onError?: (message: string) => void;
    },
    signal?: AbortSignal,
  ): Promise<void> {
    return fetch(`${API}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: conversationId, doc_ids: docIds }),
      signal,
    }).then(async (res) => {
      if (!res.ok || !res.body) {
        const text = await res.text().catch(() => res.statusText);
        handlers.onError?.(text);
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buf.indexOf("\n\n")) >= 0) {
          const raw = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          let event = "message";
          const dataLines: string[] = [];
          for (const line of raw.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7).trim();
            else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
          }
          if (!dataLines.length) continue;
          let data: unknown;
          try { data = JSON.parse(dataLines.join("\n")); } catch { continue; }
          switch (event) {
            case "meta": handlers.onMeta?.((data as { conversation_id: string }).conversation_id); break;
            case "evidence": {
              const d = data as { evidences: EvidenceInfo[]; llm_ready: boolean };
              handlers.onEvidence?.(d.evidences, d.llm_ready);
              break;
            }
            case "tool": {
              const d = data as { phase: string; query: string; added?: number; evidences?: EvidenceInfo[]; total?: number };
              handlers.onTool?.({ query: d.query, added: d.added ?? null, phase: d.phase, evidences: d.evidences, total: d.total });
              break;
            }
            case "delta": handlers.onDelta?.((data as { text: string }).text); break;
            case "citations": handlers.onCitations?.((data as { citations: Citation[] }).citations); break;
            case "done": handlers.onDone?.(data as { refused: boolean }); break;
            case "error": handlers.onError?.((data as { message: string }).message); break;
          }
        }
      }
    });
  },
};
