"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export default function SettingsPage() {
  const [s, setS] = useState<Record<string, string | boolean>>({});
  const [msg, setMsg] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState("");

  useEffect(() => { api.settings().then(setS).catch(() => {}); }, []);

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
      toast("已保存");
      setTestResult("");
    } catch (e) { toast(`保存失败：${e}`); }
  }

  async function test() {
    setTesting(true);
    setTestResult("");
    try {
      const r = await api.testLlm();
      setTestResult(r.ok ? `✅ 连接正常，模型回复：${r.reply}` : `❌ ${r.error}`);
    } catch (e) { setTestResult(`❌ ${e}`); }
    finally { setTesting(false); }
  }

  return (
    <div className="page">
      <div className="content-page" style={{ maxWidth: 820 }}>
        <h2>设置</h2>
        <p className="sub">模型连接与知识库路径。API Key 仅保存在本机 SQLite，不会上传。</p>

        <div className="card">
          <h3>LLM（OpenAI 兼容接口）</h3>
          <div className="form-grid">
            <label>接口地址 Base URL</label>
            <input
              type="text" value={String(s.llm_base_url ?? "")}
              onChange={e => set("llm_base_url", e.target.value)}
              placeholder="https://api.deepseek.com"
            />
            <label>模型名称</label>
            <input
              type="text" value={String(s.llm_model ?? "")}
              onChange={e => set("llm_model", e.target.value)}
              placeholder="deepseek-chat / glm-4.7 / qwen3:8b"
            />
            <label>API Key</label>
            <input
              type="password" value={String(s.llm_api_key ?? "")}
              onChange={e => set("llm_api_key", e.target.value)}
              placeholder={s.llm_api_key ? "已保存（输入新值覆盖）" : "sk-…"}
            />
            <div />
            <div className="form-hint">
              DeepSeek: https://api.deepseek.com ｜ 智谱: https://open.bigmodel.cn/api/paas ｜
              Ollama 本地: http://localhost:11434（Key 随意填）
            </div>
          </div>
        </div>

        <div className="card">
          <h3>知识库</h3>
          <div className="form-grid">
            <label>论文文件夹路径</label>
            <input
              type="text" value={String(s.kb_root ?? "")}
              onChange={e => set("kb_root", e.target.value)}
              placeholder="C:\Users\you\Desktop\论文"
            />
            <div />
            <div className="form-hint">「文献库」页扫描时的默认根目录；所有 PDF 与索引数据均保存在本机 backend/data/。</div>
            <label>多轮对话携带轮数</label>
            <input
              type="number" min={0} max={10} style={{ width: 100 }}
              value={String(s.history_turns ?? "6")}
              onChange={e => set("history_turns", e.target.value)}
            />
          </div>
        </div>

        <div className="card">
          <h3>嵌入模型</h3>
          <div style={{ fontSize: 13, color: "var(--text-2)" }}>
            当前：<code>{String(s.embed_model ?? "…")}</code>（本地 CPU 推理，更换模型需重建索引，第一版暂不支持在线切换）
          </div>
        </div>

        <div className="row">
          <button className="primary" onClick={save}>保存设置</button>
          <button onClick={test} disabled={testing || !s.llm_api_key}>
            {testing ? <span className="spin" /> : "测试 LLM 连接"}
          </button>
          {testResult && <span style={{ fontSize: 13 }}>{testResult}</span>}
        </div>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
