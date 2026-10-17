"use client";

import React, { createContext, useCallback, useContext, useEffect, useState } from "react";

export type Lang = "zh" | "en";

const DICT: Record<string, { zh: string; en: string }> = {
  // ---- 导航 ----
  nav_chat: { zh: "对话", en: "Chat" },
  nav_library: { zh: "文献库", en: "Library" },
  nav_settings: { zh: "设置", en: "Settings" },
  nav_backend_off: { zh: "后端未连接", en: "Backend offline" },
  nav_llm_ok: { zh: "LLM 已配置", en: "LLM connected" },
  nav_llm_no: { zh: "未配置 LLM（证据模式）", en: "No LLM (evidence mode)" },

  // ---- 聊天 ----
  chat_new: { zh: "＋ 新对话", en: "＋ New chat" },
  chat_search_conv: { zh: "搜索对话标题…", en: "Search conversations…" },
  chat_group_today: { zh: "今天", en: "Today" },
  chat_group_week: { zh: "7 天内", en: "Last 7 days" },
  chat_group_earlier: { zh: "更早", en: "Earlier" },
  chat_no_conv: { zh: "无匹配对话", en: "No matching chats" },
  chat_del_conv: { zh: "确定删除这个对话吗？", en: "Delete this conversation?" },
  chat_empty_title: { zh: "问一个文献库里的问题", en: "Ask your paper library a question" },
  chat_empty_sub: {
    zh: "每个论断都带页码级引用，点击角标回到原文核对。",
    en: "Every claim carries a page-level citation — click a badge to check the source.",
  },
  chat_empty_at: {
    zh: "在输入框输入 @ 可限定只在某几篇文献内提问。",
    en: "Type @ in the box to restrict the question to specific papers.",
  },
  chat_input_ph: {
    zh: "向文献库提问，@ 可限定文献范围…（Enter 发送，Shift+Enter 换行）",
    en: "Ask your library… @ to limit scope… (Enter to send, Shift+Enter for newline)",
  },
  chat_send: { zh: "发送", en: "Send" },
  chat_stop: { zh: "⏹ 停止", en: "⏹ Stop" },
  chat_thinking: { zh: "正在思考…", en: "Thinking…" },
  chat_searching: { zh: "正在检索文献库…", en: "Searching the library…" },
  chat_found: { zh: "已找到 {n} 个证据块，正在生成带引用的回答…", en: "Found {n} evidence chunks, generating a cited answer…" },
  chat_generating: { zh: "检索中 · {n} 块证据", en: "Searching · {n} chunks" },
  chat_generating_tools: { zh: "检索中 · {n} 块证据 · {m} 次补充检索", en: "Searching · {n} chunks · {m} follow-ups" },
  chat_process_done: { zh: "检索过程 · {n} 块证据", en: "Retrieval · {n} chunks" },
  chat_process_done_tools: { zh: "检索过程 · {n} 块证据 · {m} 次补充检索", en: "Retrieval · {n} chunks · {m} follow-ups" },
  chat_tool_pending: { zh: "⏳ 补充检索「{q}」", en: "⏳ Follow-up search: {q}" },
  chat_tool_done: { zh: "🔍 补充检索「{q}」", en: "🔍 Follow-up search: {q}" },
  chat_tool_added: { zh: " ＋{n} 块", en: " ＋{n} chunks" },
  chat_stopped: { zh: "⏹ 已停止生成", en: "⏹ Generation stopped" },
  chat_copy: { zh: "⧉ 复制", en: "⧉ Copy" },
  chat_copied: { zh: "✓ 已复制", en: "✓ Copied" },
  chat_reask: { zh: "↻ 重新提问", en: "↻ Re-ask" },
  chat_verified: { zh: "✓ {n} 条引用已核验", en: "✓ {n} citations verified" },
  chat_scope_hint: { zh: "限定范围：", en: "Scope:" },
  chat_scope_clear: { zh: "清除全部", en: "Clear all" },
  chat_mention_head: { zh: "限定到文献（可多次 @ 多选）", en: "Limit to papers (repeat @ to pick more)" },
  chat_del_doc_tip: { zh: "移除", en: "Remove" },
  role_you: { zh: "你", en: "You" },
  role_cortex: { zh: "Cortex", en: "Cortex" },
  sugg_1: { zh: "Swin-Unet 的编码器是怎么设计的？", en: "How is the Swin-Unet encoder designed?" },
  sugg_2: { zh: "U2-Net 用于什么任务，效果如何？", en: "What is U2-Net for and how does it perform?" },
  sugg_3: { zh: "库里的论文用了哪些医学图像分割数据集？", en: "Which medical segmentation datasets do my papers use?" },
  sugg_4: { zh: "VMamba 相比 ViT 的优势是什么？", en: "What advantages does VMamba have over ViT?" },
  chat_pdf_open: { zh: "收起原文面板", en: "Collapse source panel" },
  chat_pdf_closed: { zh: "打开原文面板", en: "Expand source panel" },
  chat_scope_del: { zh: "移除", en: "Remove" },

  // ---- 文献库 ----
  lib_title: { zh: "文献库", en: "Library" },
  lib_sub: {
    zh: "扫描论文文件夹并导入。导入 = 解析 → 切块 → 嵌入 → 索引，全部本地完成。",
    en: "Scan a paper folder and import. Import = parse → chunk → embed → index, all local.",
  },
  lib_scan_card: { zh: "扫描文件夹", en: "Scan folder" },
  lib_scan_ph: { zh: "知识库根目录，例如 C:\\Users\\you\\Desktop\\papers", en: "Library root, e.g. C:\\Users\\you\\Desktop\\papers" },
  lib_scan_btn: { zh: "扫描", en: "Scan" },
  lib_found: { zh: "共 {n} 个 PDF，已选 {m} 个", en: "{n} PDFs found, {m} selected" },
  lib_filter_ph: { zh: "按文件名过滤…", en: "Filter by filename…" },
  lib_select_all: { zh: "全选", en: "Select all" },
  lib_clear_sel: { zh: "清空", en: "Clear" },
  lib_import: { zh: "导入所选", en: "Import selected" },
  lib_col_file: { zh: "文件", en: "File" },
  lib_col_size: { zh: "大小", en: "Size" },
  lib_col_status: { zh: "状态", en: "Status" },
  lib_known: { zh: "库中已有", en: "In library" },
  lib_importing: { zh: "正在导入", en: "Importing" },
  lib_cancel: { zh: "取消", en: "Cancel" },
  lib_cancel_req: { zh: "已请求取消", en: "Cancellation requested" },
  lib_n_failed: { zh: "{n} 个失败", en: "{n} failed" },
  lib_docs: { zh: "已导入文档（{n}）", en: "Documents ({n})" },
  lib_failed_hint: { zh: "{n} 篇失败，可重试", en: "{n} failed — retryable" },
  lib_col_title: { zh: "标题", en: "Title" },
  lib_col_pages: { zh: "页数", en: "Pages" },
  lib_col_chunks: { zh: "块数", en: "Chunks" },
  lib_col_time: { zh: "导入时间", en: "Imported" },
  lib_col_venue: { zh: "来源", en: "Venue" },
  lib_all: { zh: "全部", en: "All" },
  lib_filter_cat: { zh: "类别", en: "Category" },
  lib_filter_venue: { zh: "来源", en: "Venue" },
  lib_uncategorized: { zh: "根目录", en: "Root" },
  lib_retry: { zh: "重试", en: "Retry" },
  lib_delete: { zh: "删除", en: "Delete" },
  lib_retry_done: { zh: "已重新解析", en: "Re-parsed" },
  lib_retry_fail: { zh: "重试失败，请稍后再试", en: "Retry failed, try again later" },
  lib_del_confirm: {
    zh: "确定从知识库删除《{name}》吗？\n（只删除索引，不会删除磁盘上的 PDF 文件）",
    en: "Remove \"{name}\" from the library?\n(Only the index is removed; the PDF on disk is untouched)",
  },
  lib_deleted: { zh: "已删除", en: "Deleted" },
  lib_none: { zh: "还没有文档。扫描文件夹并导入即可开始提问。", en: "No documents yet. Scan a folder and import to start asking." },
  lib_started: { zh: "已开始导入 {n} 个文件", en: "Importing {n} files" },
  lib_start_fail: { zh: "未能开始", en: "Could not start" },
  lib_no_sel: { zh: "未选择任何文件", en: "No files selected" },
  lib_scan_ok: { zh: "扫描到 {n} 个 PDF", en: "Found {n} PDFs" },
  lib_scan_fail: { zh: "扫描失败：{e}", en: "Scan failed: {e}" },
  lib_ing_fail: { zh: "导入失败：{e}", en: "Import failed: {e}" },
  status_indexed: { zh: "已收录", en: "Indexed" },
  status_pending: { zh: "等待", en: "Queued" },
  status_parsing: { zh: "解析中", en: "Parsing" },
  status_failed: { zh: "失败", en: "Failed" },

  // ---- 设置 ----
  set_title: { zh: "设置", en: "Settings" },
  set_sub: {
    zh: "模型连接与知识库路径。API Key 仅保存在本机 SQLite，不会上传。",
    en: "Model connection and library path. The API Key is stored only in local SQLite, never uploaded.",
  },
  set_llm_card: { zh: "LLM（OpenAI 兼容接口）", en: "LLM (OpenAI-compatible)" },
  set_base_url: { zh: "接口地址 Base URL", en: "Base URL" },
  set_model: { zh: "模型名称", en: "Model" },
  set_api_key: { zh: "API Key", en: "API Key" },
  set_key_saved: { zh: "已保存（输入新值覆盖）", en: "Saved (type to replace)" },
  set_kb_card: { zh: "知识库", en: "Knowledge base" },
  set_kb_root: { zh: "论文文件夹路径", en: "Papers folder" },
  set_kb_hint: {
    zh: "「文献库」页扫描时的默认根目录；所有 PDF 与索引数据均保存在本机 backend/data/。",
    en: "Default root for the Library page scan; all PDFs and indexes stay in local backend/data/.",
  },
  set_history: { zh: "多轮对话携带轮数", en: "History turns" },
  set_embed_card: { zh: "嵌入模型", en: "Embedding model" },
  set_embed_now: { zh: "当前：", en: "Current: " },
  set_embed_hint: {
    zh: "（本地 CPU 推理，更换模型需重建索引，第一版暂不支持在线切换）",
    en: " (local CPU inference; switching models needs re-indexing, not supported in v1)",
  },
  set_memory_card: { zh: "长期记忆", en: "Long-term memory" },
  set_memory_on: { zh: "开启：从对话中沉淀你的研究方向与偏好，提问时自动参考", en: "On: distills your research focus and preferences from chats for future answers" },
  set_memory_clear: { zh: "清空记忆", en: "Clear memories" },
  set_memory_none: {
    zh: "还没有记忆。多问几轮后，Cortex 会自动记住你的研究方向与偏好（此处的记忆只保存在本机）。",
    en: "No memories yet. After a few chats Cortex will remember your focus and preferences (stored locally only).",
  },
  set_col_type: { zh: "类型", en: "Type" },
  set_col_content: { zh: "内容", en: "Content" },
  set_save: { zh: "保存设置", en: "Save" },
  set_test: { zh: "测试 LLM 连接", en: "Test LLM" },
  set_saved: { zh: "已保存", en: "Saved" },
  set_save_fail: { zh: "保存失败：{e}", en: "Save failed: {e}" },
  set_mem_off: { zh: "已关闭记忆", en: "Memory off" },
  set_mem_on: { zh: "已开启记忆", en: "Memory on" },
  set_mem_cleared: { zh: "已清空", en: "Cleared" },
  set_del_confirm: { zh: "确定清空全部长期记忆吗？", en: "Clear all long-term memories?" },

  // ---- PDF 面板 ----
  pdf_page: { zh: "第 {p} / {n} 页", en: "Page {p} / {n}" },
  pdf_loading: { zh: "正在打开 PDF…", en: "Opening PDF…" },
  pdf_load_fail: { zh: "PDF 加载失败：{e}", en: "Failed to load PDF: {e}" },
  pdf_render_fail: { zh: "页面渲染失败：{e}", en: "Page render failed: {e}" },
  pdf_empty_tip: {
    zh: "点击回答中的引用角标\n这里会打开原文对应页并高亮证据区域",
    en: "Click a citation badge in an answer\nto open the source page with the evidence highlighted",
  },
};

interface LangCtx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const Ctx = createContext<LangCtx>({ lang: "zh", setLang: () => {}, t: (k) => k });

export function LangProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>("zh");

  useEffect(() => {
    const saved = window.localStorage.getItem("cortex_lang");
    if (saved === "en" || saved === "zh") setLangState(saved);
  }, []);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    window.localStorage.setItem("cortex_lang", l);
    document.documentElement.lang = l === "zh" ? "zh-CN" : "en";
  }, []);

  const t = useCallback((key: string, params?: Record<string, string | number>) => {
    const entry = DICT[key];
    let text = entry ? entry[lang] : key;
    if (params) {
      for (const [k, v] of Object.entries(params)) {
        text = text.replaceAll(`{${k}}`, String(v));
      }
    }
    return text;
  }, [lang]);

  return <Ctx.Provider value={{ lang, setLang, t }}>{children}</Ctx.Provider>;
}

export function useLang() {
  return useContext(Ctx);
}
