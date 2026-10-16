"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, DocumentInfo, IngestJob } from "@/lib/api";

export default function LibraryPage() {
  const [docs, setDocs] = useState<DocumentInfo[]>([]);
  const [job, setJob] = useState<IngestJob | null>(null);
  const [root, setRoot] = useState("");
  const [scanned, setScanned] = useState<{ path: string; filename: string; size_mb: number; known: boolean }[] | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState("");
  const [scanning, setScanning] = useState(false);
  const [filter, setFilter] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const toast = (t: string) => { setMsg(t); setTimeout(() => setMsg(""), 3500); };

  const refresh = useCallback(async () => {
    try {
      setDocs(await api.documents());
      const j = await api.job();
      setJob(j);
      return j;
    } catch { return null; }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // 摄取进行中轮询
  useEffect(() => {
    if (job?.state === "running") {
      pollRef.current = setInterval(() => {
        api.job().then(j => {
          setJob(j);
          if (j.state !== "running") refresh();
        }).catch(() => {});
      }, 1500);
      return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }
  }, [job?.state, refresh]);

  async function scan() {
    setScanning(true);
    try {
      const res = await api.scan(root);
      setScanned(res.files);
      setSelected(new Set(res.files.filter(f => !f.known).map(f => f.path)));
      toast(`扫描到 ${res.total} 个 PDF`);
    } catch (e) {
      toast(`扫描失败：${e}`);
    } finally { setScanning(false); }
  }

  async function ingestSelected(incremental: boolean) {
    const paths = [...selected];
    if (!paths.length) { toast("未选择任何文件"); return; }
    try {
      const res = await api.ingest(paths, incremental);
      if (res.started) toast(`已开始导入 ${res.queued} 个文件`);
      else toast(res.reason || "未能开始");
      refresh();
    } catch (e) { toast(`导入失败：${e}`); }
  }

  async function retry(id: string) {
    await api.retryDocument(id);
    toast("已重新解析");
    refresh();
  }

  async function remove(id: string) {
    await api.deleteDocument(id);
    refresh();
  }

  const filtered = scanned?.filter(f => !filter || f.filename.toLowerCase().includes(filter.toLowerCase())) ?? null;
  const running = job?.state === "running";

  return (
    <div className="page">
      <div className="content-page">
        <h2>文献库</h2>
        <p className="sub">扫描论文文件夹并导入。导入 = 解析 → 切块 → 嵌入 → 索引，全部本地完成。</p>

        {running && job && (
          <div className="card">
            <h3>正在导入 <span className="spin" /></h3>
            <div className="row" style={{ marginBottom: 8 }}>
              <span className="grow">{job.current_file || "…"}</span>
              <span style={{ color: "var(--text-2)" }}>{job.done + job.failed} / {job.total}</span>
              <button className="small danger" onClick={() => api.cancelJob().then(() => toast("已请求取消"))}>取消</button>
            </div>
            <div className="progress-bar">
              <div style={{ width: `${job.total ? ((job.done + job.failed) / job.total) * 100 : 0}%` }} />
            </div>
            {job.failed > 0 && <div style={{ color: "var(--red)", fontSize: 12, marginTop: 6 }}>{job.failed} 个失败</div>}
          </div>
        )}

        <div className="card">
          <h3>扫描文件夹</h3>
          <div className="row">
            <input
              type="text" className="grow" value={root}
              onChange={e => setRoot(e.target.value)}
              placeholder="知识库根目录，例如 C:\Users\you\Desktop\论文"
            />
            <button onClick={scan} disabled={scanning || !root}>
              {scanning ? <span className="spin" /> : "扫描"}
            </button>
          </div>
          {scanned && (
            <>
              <div className="row wrap" style={{ marginTop: 12 }}>
                <span style={{ fontSize: 13, color: "var(--text-2)" }}>
                  共 {scanned.length} 个 PDF，已选 {selected.size} 个
                </span>
                <input
                  type="text" placeholder="按文件名过滤…" value={filter}
                  onChange={e => setFilter(e.target.value)} style={{ width: 200 }}
                />
                <span style={{ flex: 1 }} />
                <button onClick={() => setSelected(new Set(filtered!.map(f => f.path)))}>全选</button>
                <button onClick={() => setSelected(new Set())}>清空</button>
                <button className="primary" onClick={() => ingestSelected(true)}>导入所选</button>
              </div>
              <div style={{ maxHeight: 300, overflowY: "auto", marginTop: 10, border: "1px solid var(--border)", borderRadius: 8 }}>
                <table className="tbl">
                  <thead>
                    <tr><th style={{ width: 34 }}></th><th>文件</th><th>大小</th><th>状态</th></tr>
                  </thead>
                  <tbody>
                    {filtered!.map(f => (
                      <tr key={f.path}>
                        <td>
                          <input
                            type="checkbox" checked={selected.has(f.path)}
                            onChange={e => {
                              const next = new Set(selected);
                              if (e.target.checked) next.add(f.path); else next.delete(f.path);
                              setSelected(next);
                            }}
                          />
                        </td>
                        <td style={{ maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={f.path}>
                          {f.filename}
                        </td>
                        <td style={{ whiteSpace: "nowrap" }}>{f.size_mb} MB</td>
                        <td>{f.known && <span className="badge indexed">库中已有</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <div className="card">
          <h3>已导入文档（{docs.length}）</h3>
          {docs.length === 0 ? (
            <div className="empty-state" style={{ padding: "30px 0" }}>
              <div>还没有文档。扫描文件夹并导入即可开始提问。</div>
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>标题</th><th>状态</th><th>页数</th><th>块数</th><th>导入时间</th><th style={{ width: 130 }}></th>
                </tr>
              </thead>
              <tbody>
                {docs.map(d => (
                  <tr key={d.id}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{d.title || d.filename}</div>
                      <div style={{ fontSize: 12, color: "var(--text-3)", maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {d.filename}{d.error ? ` — ${d.error}` : ""}
                      </div>
                    </td>
                    <td><span className={`badge ${d.status}`}>{d.status}</span></td>
                    <td>{d.n_pages || "—"}</td>
                    <td>{d.n_chunks || "—"}</td>
                    <td style={{ whiteSpace: "nowrap", color: "var(--text-2)" }}>{(d.indexed_at || d.created_at || "").slice(0, 16)}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        {d.status === "failed" && <button className="small" onClick={() => retry(d.id)}>重试</button>}
                        <button className="small danger" onClick={() => remove(d.id)}>删除</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {msg && <div className="toast">{msg}</div>}
    </div>
  );
}
