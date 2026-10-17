"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, DocumentInfo, IngestJob } from "@/lib/api";
import { useLang } from "@/components/Lang";

export default function LibraryPage() {
  const { t } = useLang();
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
      toast(t("lib_scan_ok", { n: res.total }));
    } catch (e) {
      toast(t("lib_scan_fail", { e: String(e) }));
    } finally { setScanning(false); }
  }

  async function ingestSelected(incremental: boolean) {
    const paths = [...selected];
    if (!paths.length) { toast(t("lib_no_sel")); return; }
    try {
      const res = await api.ingest(paths, incremental);
      if (res.started) toast(t("lib_started", { n: res.queued ?? 0 }));
      else toast(res.reason || t("lib_start_fail"));
      refresh();
    } catch (e) { toast(t("lib_ing_fail", { e: String(e) })); }
  }

  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [venueFilter, setVenueFilter] = useState("");
  const [catFilter, setCatFilter] = useState("");

  async function retry(id: string) {
    setRetryingId(id);
    try {
      await api.retryDocument(id);
      toast(t("lib_retry_done"));
    } catch { toast(t("lib_retry_fail")); }
    finally { setRetryingId(null); }
    refresh();
  }

  async function remove(id: string, filename: string) {
    if (!window.confirm(t("lib_del_confirm", { name: filename }))) return;
    await api.deleteDocument(id);
    toast(t("lib_deleted"));
    refresh();
  }

  const venues = [...new Set(docs.map(d => d.venue).filter((v): v is string => !!v))].sort((a, b) => a.localeCompare(b));
  const categories = [...new Set(docs.map(d => d.category).filter((c): c is string => !!c))].sort((a, b) => a.localeCompare(b));
  const visibleDocs = docs.filter(d =>
    (!venueFilter || d.venue === venueFilter) && (!catFilter || d.category === catFilter));

  const filtered = scanned?.filter(f => !filter || f.filename.toLowerCase().includes(filter.toLowerCase())) ?? null;
  const running = job?.state === "running";

  return (
    <div className="page">
      <div className="content-page">
        <h2>{t("lib_title")}</h2>
        <p className="sub">{t("lib_sub")}</p>

        {running && job && (
          <div className="card">
            <h3>{t("lib_importing")} <span className="spin" /></h3>
            <div className="row" style={{ marginBottom: 8 }}>
              <span className="grow">{job.current_file || "…"}</span>
              <span style={{ color: "var(--text-2)" }}>{job.done + job.failed} / {job.total}</span>
              <button className="small danger" onClick={() => api.cancelJob().then(() => toast(t("lib_cancel_req")))}>{t("lib_cancel")}</button>
            </div>
            <div className="progress-bar">
              <div style={{ width: `${job.total ? ((job.done + job.failed) / job.total) * 100 : 0}%` }} />
            </div>
            {job.failed > 0 && <div style={{ color: "var(--red)", fontSize: 12, marginTop: 6 }}>{t("lib_n_failed", { n: job.failed })}</div>}
          </div>
        )}

        <div className="card">
          <h3>{t("lib_scan_card")}</h3>
          <div className="row">
            <input
              type="text" className="grow" value={root}
              onChange={e => setRoot(e.target.value)}
              placeholder={t("lib_scan_ph")}
            />
            <button onClick={scan} disabled={scanning || !root}>
              {scanning ? <span className="spin" /> : t("lib_scan_btn")}
            </button>
          </div>
          {scanned && (
            <>
              <div className="row wrap" style={{ marginTop: 12 }}>
                <span style={{ fontSize: 13, color: "var(--text-2)" }}>
                  {t("lib_found", { n: scanned.length, m: selected.size })}
                </span>
                <input
                  type="text" placeholder={t("lib_filter_ph")} value={filter}
                  onChange={e => setFilter(e.target.value)} style={{ width: 200 }}
                />
                <span style={{ flex: 1 }} />
                <button onClick={() => setSelected(new Set(filtered!.map(f => f.path)))}>{t("lib_select_all")}</button>
                <button onClick={() => setSelected(new Set())}>{t("lib_clear_sel")}</button>
                <button className="primary" onClick={() => ingestSelected(true)}>{t("lib_import")}</button>
              </div>
              <div style={{ maxHeight: 300, overflowY: "auto", marginTop: 10, border: "1px solid var(--border)", borderRadius: 8 }}>
                <table className="tbl">
                  <thead>
                    <tr><th style={{ width: 34 }}></th><th>{t("lib_col_file")}</th><th>{t("lib_col_size")}</th><th>{t("lib_col_status")}</th></tr>
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
                        <td>{f.known && <span className="badge indexed">{t("lib_known")}</span>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>

        <div className="card">
          <div className="row wrap" style={{ marginBottom: 12 }}>
            <span style={{ fontSize: 13, color: "var(--text-2)" }}>{t("lib_filter_cat")}</span>
            <select style={{ width: 160 }} value={catFilter} onChange={e => setCatFilter(e.target.value)}>
              <option value="">{t("lib_all")}</option>
              {categories.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <span style={{ fontSize: 13, color: "var(--text-2)" }}>{t("lib_filter_venue")}</span>
            <select style={{ width: 160 }} value={venueFilter} onChange={e => setVenueFilter(e.target.value)}>
              <option value="">{t("lib_all")}</option>
              {venues.map(v => <option key={v} value={v}>{v}</option>)}
            </select>
            {(venueFilter || catFilter) && (
              <button className="small" onClick={() => { setVenueFilter(""); setCatFilter(""); }}>
                {t("chat_scope_clear")}
              </button>
            )}
          </div>
          <h3>
            {t("lib_docs", { n: visibleDocs.length })}
            {visibleDocs.some(d => d.status === "failed") && (
              <span style={{ fontSize: 12, color: "var(--red)", fontWeight: 400, marginLeft: 10 }}>
                {t("lib_failed_hint", { n: visibleDocs.filter(d => d.status === "failed").length })}
              </span>
            )}
          </h3>
          {docs.length === 0 ? (
            <div className="empty-state" style={{ padding: "30px 0" }}>
              <div>{t("lib_none")}</div>
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>{t("lib_col_title")}</th><th>{t("lib_col_venue")}</th><th>{t("lib_col_status")}</th><th>{t("lib_col_pages")}</th><th>{t("lib_col_chunks")}</th><th>{t("lib_col_time")}</th><th style={{ width: 130 }}></th>
                </tr>
              </thead>
              <tbody>
                {visibleDocs.map(d => (
                  <tr key={d.id}>
                    <td>
                      <div style={{ fontWeight: 500 }}>{d.title || d.filename}</div>
                      <div style={{ fontSize: 12, color: "var(--text-3)", maxWidth: 480, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {d.filename}{d.error ? ` — ${d.error}` : ""}
                      </div>
                    </td>
                    <td>{d.venue ? <span className="badge pending">{d.venue}</span> : <span style={{ color: "var(--text-3)" }}>—</span>}</td>
                    <td><span className={`badge ${d.status}`}>{t(`status_${d.status}`)}</span></td>
                    <td>{d.n_pages || "—"}</td>
                    <td>{d.n_chunks || "—"}</td>
                    <td style={{ whiteSpace: "nowrap", color: "var(--text-2)" }}>{(d.indexed_at || d.created_at || "").slice(0, 16)}</td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        {d.status === "failed" && (
                          <button className="small" onClick={() => retry(d.id)} disabled={retryingId !== null}>
                            {retryingId === d.id ? <span className="spin" /> : t("lib_retry")}
                          </button>
                        )}
                        <button className="small danger" onClick={() => remove(d.id, d.title || d.filename)}>{t("lib_delete")}</button>
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
