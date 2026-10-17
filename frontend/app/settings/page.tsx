"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useLang } from "@/components/Lang";

export default function SettingsPage() {
  const { t } = useLang();
  const [s, setS] = useState<Record<string, string | boolean>>({});
  const [msg, setMsg] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState("");
  const [memories, setMemories] = useState<{ id: number; kind: string; content: string; created_at: string }[]>([]);
  const [memEnabled, setMemEnabled] = useState(true);
  const [rerankEnabled, setRerankEnabled] = useState(true);

  const loadMemories = () => {
    api.memories().then(r => { setMemories(r.items); setMemEnabled(r.enabled); }).catch(() => {});
  };
  useEffect(() => {
    api.settings().then(s => { setS(s); setRerankEnabled(s.rerank_enabled === true || s.rerank_enabled === "true"); }).catch(() => {});
    loadMemories();
  }, []);

  const toast = (t: string) => { setMsg(t); setTimeout(() => setMsg(""), 3000); };

  function set(k: string, v: string) { setS(prev => ({ ...prev, [k]: v })); }

  async function save() {
    const patch: Record<string, string> = {};
    for (const k of ["kb_root", "llm_base_url", "llm_api_key", "llm_model", "history_turns"]) {
      const v = s[k];
      if (typeof v === "string" && v) patch[k] = v;
    }
    try {
      const updated = await api.saveSettings(patch);
      setS(updated);
      toast(t("set_saved"));
      setTestResult("");
    } catch (e) { toast(t("set_save_fail", { e: String(e) })); }
  }

  async function test() {
    setTesting(true);
    setTestResult("");
    try {
      const r = await api.testLlm();
      setTestResult(r.ok ? `✅ ${r.reply}` : `❌ ${r.error}`);
    } catch (e) { setTestResult(`❌ ${e}`); }
    finally { setTesting(false); }
  }

  return (
    <div className="page">
      <div className="content-page" style={{ maxWidth: 820 }}>
        <h2>{t("set_title")}</h2>
        <p className="sub">{t("set_sub")}</p>

        <div className="card">
          <h3>{t("set_llm_card")}</h3>
          <div className="form-grid">
            <label>{t("set_base_url")}</label>
            <input
              type="text" value={String(s.llm_base_url ?? "")}
              onChange={e => set("llm_base_url", e.target.value)}
              placeholder="https://api.deepseek.com"
            />
            <label>{t("set_model")}</label>
            <input
              type="text" value={String(s.llm_model ?? "")}
              onChange={e => set("llm_model", e.target.value)}
              placeholder="deepseek-chat / glm-4.7 / qwen3:8b"
            />
            <label>{t("set_api_key")}</label>
            <input
              type="password" value={String(s.llm_api_key ?? "")}
              onChange={e => set("llm_api_key", e.target.value)}
              placeholder={s.llm_api_key ? t("set_key_saved") : "sk-…"}
            />
            <div />
            <div className="form-hint">
              DeepSeek: https://api.deepseek.com ｜ 智谱: https://open.bigmodel.cn/api/paas ｜
              Ollama 本地: http://localhost:11434（Key 随意填）
            </div>
          </div>
        </div>

        <div className="card">
          <h3>{t("set_kb_card")}</h3>
          <div className="form-grid">
            <label>{t("set_kb_root")}</label>
            <input
              type="text" value={String(s.kb_root ?? "")}
              onChange={e => set("kb_root", e.target.value)}
              placeholder="C:\Users\you\Desktop\论文"
            />
            <div />
            <div className="form-hint">{t("set_kb_hint")}</div>
            <label>{t("set_history")}</label>
            <input
              type="number" min={0} max={10} style={{ width: 100 }}
              value={String(s.history_turns ?? "6")}
              onChange={e => set("history_turns", e.target.value)}
            />
          </div>
        </div>

        <div className="card">
          <h3>{t("set_rerank_card")}</h3>
          <label className="checkbox-row">
            <input
              type="checkbox" checked={rerankEnabled}
              onChange={async e => {
                setRerankEnabled(e.target.checked);
                await fetch(`${process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8300"}/api/settings`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ rerank_enabled: e.target.checked ? "on" : "off" }),
                });
                toast(t("set_saved"));
              }}
            />
            {t("set_rerank_on")}
          </label>
        </div>

        <div className="card">
          <h3>{t("set_embed_card")}</h3>
          <div style={{ fontSize: 13, color: "var(--text-2)" }}>
            {t("set_embed_now")}<code>{String(s.embed_model ?? "…")}</code>{t("set_embed_hint")}
          </div>
        </div>

        <div className="card">
          <h3>{t("set_memory_card")}</h3>
          <div className="row" style={{ marginBottom: 10 }}>
            <label className="checkbox-row">
              <input
                type="checkbox" checked={memEnabled}
                onChange={async e => {
                  setMemEnabled(e.target.checked);
                  await api.toggleMemory(e.target.checked);
                  toast(e.target.checked ? t("set_mem_on") : t("set_mem_off"));
                }}
              />
              {t("set_memory_on")}
            </label>
            <span style={{ flex: 1 }} />
            <button
              className="small danger"
              onClick={async () => {
                if (!window.confirm(t("set_del_confirm"))) return;
                await api.clearMemories();
                loadMemories();
                toast("已清空");
              }}
              disabled={!memories.length}
            >{t("set_memory_clear")}</button>
          </div>
          {memories.length === 0 ? (
            <div style={{ fontSize: 13, color: "var(--text-3)", padding: "8px 0" }}>
              {t("set_memory_none")}
            </div>
          ) : (
            <table className="tbl">
              <thead><tr><th style={{ width: 110 }}>{t("set_col_type")}</th><th>{t("set_col_content")}</th><th style={{ width: 40 }}></th></tr></thead>
              <tbody>
                {memories.map(m => (
                  <tr key={m.id}>
                    <td><span className="badge pending">{m.kind}</span></td>
                    <td>{m.content}</td>
                    <td>
                      <button
                        className="small danger"
                        style={{ border: "none", background: "transparent" }}
                        onClick={async () => { await api.deleteMemory(m.id); loadMemories(); }}
                      >✕</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <div className="row">
          <button className="primary" onClick={save}>{t("set_save")}</button>
          <button onClick={test} disabled={testing || !s.llm_api_key}>
            {testing ? <span className="spin" /> : t("set_test")}
          </button>
          {testResult && <span style={{ fontSize: 13 }}>{testResult}</span>}
        </div>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
