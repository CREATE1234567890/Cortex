# Cortex — 个人科研知识库问答 Agent（v0.1）

以「页码级可溯源引用」为核心承诺的个人文献库问答助手：把论文文件夹导入后，即可用自然语言提问，每个论断都带 `[n]` 引用角标，点击即可打开原文 PDF 对应页并高亮证据区域。

对应调研文档见 [docs/research/](docs/research/)（需求/竞品/选型/架构五份报告）。本仓库是 MVP 第一版落地。

## 已实现（对应需求编号）

| 模块 | 需求 | 说明 |
|---|---|---|
| 批量导入 | FR-KB-01/03 | 递归扫描文件夹、sha256 内容去重、批量摄取、进度条、失败可见可重试 |
| PDF 解析 | FR-DP-01/02 | PyMuPDF：双栏重排、页眉页脚剔除、标题层级、表格 markdown 化，全块携带页码+bbox |
| 语义切块 | FR-DP-03 | section-aware：正文 400–600 token、15% 重叠；摘要/表格独立成块；chunk 携带 section_path/page/bbox |
| 混合检索 | FR-IR-01/02 | 稠密(multilingual-e5-small) + BM25(FTS5) → RRF 融合；按文献过滤（提问范围） |
| 可溯源问答 | FR-QA-01/02 | SSE 流式输出、句级 `[n]` 引用、证据块先行展示 |
| 诚实拒答 | FR-QA-03 | 无证据时明确拒答并给出已检索的最接近内容 |
| 多轮对话 | FR-QA-04 | 会话历史携带、会话管理（侧栏） |
| 引用验证 | FR-CT-02 | 生成后校验：`[n]` 不在证据集内的引用直接剔除（防幻觉引用） |
| 模型配置 | FR-SM-01 | OpenAI 兼容接口（DeepSeek/GLM/Ollama…），设置页可改、可测连 |
| 本地优先 | FR-SM-02 | PDF/索引/对话记录全部本地（SQLite + numpy + 文件系统） |

**未实现（下一版）**：Zotero 同步、rerank 精排、元数据补全（DOI/CrossRef）、综述生成 Agent、引用验证 entailment 复核、Markdown 笔记、成本面板、扫描件 OCR。

## 架构（第一版落地形态）

```
论文文件夹 ─► 扫描/去重 ─► PyMuPDF 解析（页码+bbox+章节） ─► section-aware 切块
      ─► e5 嵌入(numpy 向量库) + FTS5 BM25 ─► RRF 混合检索 ─► 证据组装[1..8]
      ─► LLM 生成（强制 [n] 引用，无证据拒答） ─► 引用验证剔除 ─► SSE 推流
      ─► 前端点击 [n] ─► pdf.js 打开原文页 + bbox 高亮
```

技术栈：**FastAPI + SQLite(FTS5) + sentence-transformers(CPU) + numpy 向量库 + Next.js 15 + pdf.js**。
原方案中的 Qdrant/MinerU/LangGraph（需 Docker/GPU）留待 V1 替换升级——检索/生成接口已按可替换设计（`indexer.py`、`llm.py`）。

摄取流水线把解析/切块/嵌入放在**独立子进程**执行（`library.py`）：不占主进程 GIL（摄取期间界面保持响应），单篇 5 分钟超时强杀（病理 PDF 不拖垮服务），失败标红可重试。

## 运行

### 0. 环境要求
- Windows / Python 3.10+ / Node 20+
- 无需 Docker 和 GPU（嵌入与检索全 CPU 本地跑）

### 1. 后端

```bash
cd backend
pip install -r requirements.txt
# 首次会自动下载嵌入模型（已内置本地副本 data/models/multilingual-e5-small 则免联网）
set PYTHONUTF8=1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8300
```

### 2. 前端

```bash
cd frontend
npm install        # postinstall 会自动拷贝 pdf.js worker 到 public/
npm run dev        # http://localhost:3000
```

或直接用根目录脚本（双击/命令行）：`start.bat` 同时拉起前后端。

### 3. 使用流程
1. 打开 http://localhost:3000 → 「文献库」→ 填论文文件夹路径 → 扫描 → 全选 → 导入（后台跑，进度可见）
2. 回到「对话」页提问；回答中的数字角标 `[n]` 点击即跳原文页并高亮
3. 「设置」页填 LLM API Key（如 DeepSeek）。**不填也能用**：降级为「证据列举模式」，直接展示检索证据与页码

## 配置

| 项 | 位置 | 默认 |
|---|---|---|
| 知识库路径 | 设置页 / `kb_root` | `C:\Users\…\Desktop\论文文件夹` |
| LLM 接口 | 设置页 / `llm_base_url`+`llm_api_key`+`llm_model` | DeepSeek `deepseek-chat` |
| 嵌入模型 | `backend/app/config.py` | `intfloat/multilingual-e5-small`（本地副本） |
| 检索参数 | `backend/app/config.py` | dense 24 + bm25 24 → RRF → top-8 |

数据都在 `backend/data/`：`cortex.db`（元数据/会话/设置/FTS）、`vectors/`（向量索引）、`models/`（嵌入模型副本）。备份 = 拷走整个目录。

## 已知问题与修复记录

- **pdf.js 渲染挂起**（嵌入式浏览器不触发 requestAnimationFrame）：`frontend/lib/raf.ts` 自动探测并降级为 setTimeout，普通浏览器无感知。
- transformers 5.x 与 torch 2.7 不兼容：requirements 已锁 `transformers<5`。
- Windows 控制台中文乱码：启动脚本已设 `PYTHONUTF8=1`。
- 扫描件 PDF 无法解析（无 OCR 通道），失败会在文献库列表标红，不影响其他文档。

## 下一步（V1 建议顺序）

1. rerank 精排（bge-reranker-v2-m3 CPU 通道）与查询改写
2. 元数据补全（标题→DOI/作者/年份，CrossRef/Semantic Scholar）+ DOI 去重
3. 引用验证升级：字面重叠 + NLI entailment 双通道复核
4. 综述生成 Agent（LangGraph 状态机：规划→迭代检索→大纲→分节写作）
5. 评测集回归（100 条 golden set，引用正确率 ≥95% 门禁）
