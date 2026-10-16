# Cortex 项目调研总览 — 个人知识库问答 Agent（深度科研研究助手）

> 调研完成时间：2026-10-16 ｜ 方法：5 个并行调研 agent（需求调研 / 需求分析 / 应用调研 / 技术选型 / 技术架构），基于 2025–2026 年最新 Web 信息交叉验证。
> 本文件是五份报告的交叉整合与最终结论；各方向细节见同目录 01–05 分报告。

| 分报告 | 内容 |
|---|---|
| [01-需求调研.md](01-需求调研.md) | 用户画像、痛点、社区需求收集、机会点 |
| [02-需求分析.md](02-需求分析.md) | 产品定位、用户故事、功能/非功能需求清单、MVP 边界 |
| [03-应用调研.md](03-应用调研.md) | 竞品全景（通用 RAG / 科研垂直 / 框架 / 解析）、市场空白 |
| [04-技术选型.md](04-技术选型.md) | 分层选型对比与推荐（含版本与价格） |
| [05-技术架构.md](05-技术架构.md) | 七层架构、数据模型、深度研究 Agent、ADR、演进路线 |

---

## 一、TL;DR

**做不做**：值得做。市场空白真实存在——"个人文献库为中心 + 科研级 PDF 解析 + 页码级可验证引用 + 迭代式深度研究 + 本地优先"五者兼备的产品目前不存在（Elicit/OpenScholar 以公共语料为中心，RAGFlow/AnythingLLM 不懂科研语义，PaperQA2 有能力无产品化 UI）。

**做什么**：Cortex = 以 Zotero 个人文献库为第一数据源、以"每个论断可点击回原文页码"为信任护城河的个人科研问答与深度研究 Agent。核心三件事：① 引用锚定问答（击中"找不回 + 不可信"两大痛点）；② 综述/对比表生成（把数周压缩为数小时）；③ 笔记-文献融合检索（填补 NotebookLM/Zotero 之间的空隙）。

**用什么做（黄金组合）**：MinerU（Docker）解析 + Qdrant 混合检索（稠密+稀疏+BM25）+ Qwen3-Embedding/Reranker（本地）+ DeepSeek API（主力）与 Ollama Qwen3（本地降级）+ LangGraph 轻量编排 + FastAPI + Next.js + RAGAS/Langfuse 评估可观测。详见第三节。

**节奏**：4 周 MVP（引用可点、答案可信）→ 8 周 V1（完整 RAG + 评估门禁）→ 12 周 V2（深度研究 Agent + 图增强）。单人可维护。

---

## 二、跨方向交叉结论

### 2.1 需求侧的三个"证实"支柱（来自 01 + 02）

1. **"读不完、找不回、不可信"三角**是科研用户最强痛点（文献年增 ~5.6% 检索不动；PDF 内语义找证据难；AI 编造引用零容忍）。Cortex 所有功能都应服务于这三点，不偏离。
2. **信任是唯一护城河**：科研用户幻觉容忍度≈0，一次错引即流失。因此"引用正确率 ≥95%"被列为不可妥协指标，并配四层防线（证据先行解耦架构、拒答优先、生成后引用验证器、评测集回归门禁）。
3. **辅助而不越界**：社区明确反感"AI 替代思考"，产品定位必须是"放大研究者判断力"——证据表先行、大纲可编辑、结论可核验。

### 2.2 竞品侧的可借鉴设计（来自 03）

- **PaperQA2**：证据收集与答案生成解耦 + 引用必须落在证据池内 + 页码级引用 → 这是其在 RAG-QA Arena 科学基准 SOTA（超最强人类 12.4%）的核心，Cortex 的问答主循环照此设计。
- **gpt-researcher / STORM**：plan→迭代检索→反思补搜→大纲→带引用成文 → Cortex 深度研究模式的状态机蓝本。
- **Elicit**：结构化抽取对比表是综述刚需，每个单元格须可溯源。
- **Scite**：supporting/contrasting 引用分类，可用 LLM 在个人库内低成本复现。
- **RAGFlow**：解析质量决定 RAG 上限，解析是第一优先级模块。
- **Zotero 生态**：Zotero 是中文科研圈事实标准，"接入而非替代"是采用率关键（读其元数据+PDF+标注，免重新上传）。

### 2.3 技术侧的收敛共识（来自 04 + 05）

- 解析 > 检索花活：chunk 携带 `(doc, page, section, char_span)` 坐标是引用溯源的地基，索引 schema 必须一次设计到位。
- 混合检索（稠密+稀疏+BM25 + RRF + reranker）优于纯向量，中英混排与专名/公式符号场景尤甚。
- 轻量编排（LangGraph 状态图）优于重型多 agent 框架（Anthropic 实测多 agent token ≈15 倍，个人项目不可调试）。
- 双通道模型路由（云端强模型 + 本地 Ollama）兼顾成本、隐私与断网可用。
- V1 不上知识图谱；V2 再以 HippoRAG 2 / LightRAG 做轻量图增强。

### 2.4 两份报告的分歧与裁决

| 分歧点 | 04-技术选型 | 05-技术架构 | 裁决 |
|---|---|---|---|
| Embedding | Qwen3-Embedding-0.6B（MTEB 领先，备选 BGE-M3） | 直接用 BGE-M3（稠密+稀疏双输出省事） | **首选 Qwen3-Embedding-0.6B + 稀疏走 Qdrant 内置 BM25/fastembed**；若想单模型出双向量则换 BGE-M3。向量层接口按"稠密+稀疏"抽象，两者可互换 |
| Reranker | Qwen3-Reranker-0.6B（备选 BGE-v2-m3） | bge-reranker-v2-m3 | **Qwen3-Reranker-0.6B 起步**（与 embedding 同族协同），保守备选 BGE-v2-m3 |
| 解析版本 | MinerU 4.x（Docker/WSL2，Windows pip CLI 有坑） | MinerU 2.x + PyMuPDF 快速通道 | **MinerU 最新稳定版走 Docker + PyMuPDF 快速通道**（纯文本层 PDF 不进重解析管线），开发期可用 Docling 做 pip 通道 |
| LLM | DeepSeek Flash 主力 + V4-Pro 深研档 | 未指定具体模型 | 采纳 04 的分层路由（简单问答小模型 / 生成与 planner 强模型），具体型号实施前重验最新版本与价格 |

---

## 三、最终技术栈（裁决后）

| 层 | 选型 | 说明 |
|---|---|---|
| PDF 解析 | MinerU（Docker）主通道 + PyMuPDF 快速通道 + Docling 开发兜底 | 公式转 LaTeX、表格转 HTML、输出页码/bbox 坐标 |
| 切分 | section-aware chunking（400–600 token，15% 重叠） | 表格/公式整块独立成 chunk；元数据含 section_path/page/char_span |
| Embedding | Qwen3-Embedding-0.6B 本地（备选 BGE-M3） | 中英混合；本地零成本 |
| 向量库 | Qdrant（Docker，稠密+稀疏+RRF） | BM25 兜底走 SQLite FTS5 |
| Reranker | Qwen3-Reranker-0.6B 本地（备选 BGE-reranker-v2-m3） | top-50 → top-8 |
| LLM | 云端：DeepSeek Flash（主力）/ Pro（深研档）；本地：Ollama + Qwen3-8B | OpenAI 兼容统一封装 + 模型路由抽象 |
| 编排 | LangGraph 1.x 状态图（QA=工具循环，深研=显式状态机 + checkpoint） | 拒绝 AutoGen/CrewAI 重型框架 |
| 后端 | FastAPI + SQLite（schema 预留 Postgres 迁移） | 单用户，异步任务 |
| 前端 | Next.js + React（引用卡片 + PDF 高亮跳转）+ Streamlit 调试台 | 引用溯源 UI 是核心差异化，须自建 |
| 评估 | RAGAS + 自建引用正确率指标 + 100 条 golden set | 引用正确率 ≥95% 作为发布门禁 |
| 可观测 | Langfuse self-host | trace / token 成本 / 回归 |

**成本预期**：日常问答 + 每周深研 ≈ ¥30–80/月；全本地模式可趋近于零。
**规模预期**：1 万篇 PDF（≈30 万 chunks）检索 <500ms，Qdrant 单容器绰绰有余。

---

## 四、MVP → V1 → V2 路线（12 周）

- **MVP（1–4 周）**：PDF 批量导入 + 元数据补全去重 + Zotero 只读同步；MinerU 解析 + section 切分；稠密检索 + 页码引用问答（流式、拒答、多轮）；SQLite + Qdrant 双容器 + 最简 UI。**验收线：引用可点、答案可信。**
- **V1（5–8 周）**：完整混合检索 + rerank；引用验证 pass；DOI 去重与引用图扩展；Langfuse 追踪 + golden set 回归；深度研究状态机（检索-阅读循环）。**验收线：引用正确率 ≥95% 且评测可自动回归。**
- **V2（9–12 周）**：完整深度研究 Agent（大纲→撰写→审查→合成、断点恢复）；结构化对比表；综述生成；arXiv 主题追踪周报；HippoRAG 2/LightRAG 图增强；成本看板与全本地模式打磨。**验收线：综述覆盖 ≥90% 高相关文献且逐段可溯源。**

每个版本发布前必须过评测集回归门禁（引用正确率不回退）。

## 五、Top 5 风险

1. **幻觉引用摧毁信任**（最高风险）→ 解耦架构 + 拒答 + 引用验证器 + 发布门禁四层防线。
2. **PDF 解析质量是全系统上限**（双栏/公式/表格）→ MinerU 主通道 + 50 篇金标集回归 + 失败可见可重试。
3. **模型/框架迭代极快**（调研时点版本号实施时可能已过时）→ 模型路由抽象层 + 定期跑 RAGAS 基线防回退。
4. **深度 Agent 成本与失控循环** → 步数/Token 硬预算 + 可取消 + checkpoint 断点恢复。
5. **单人范围蔓延** → 以 02 报告的 P0/P1/P2 清单为契约，P1 项不挤入 MVP。

## 六、下一步行动建议

1. **验证假设（本周）**：按 01 报告的用户画像做 3–5 人深度访谈，验证【推测】项（报告导出、主题追踪、学科配置的真实需求）；同时安装 Zotero 确认本地库 API 可读性。
2. **技术验证 spike（1 周内可并行）**：① 用 10 篇中文 + 10 篇英文 PDF 跑 MinerU Docker 解析，人工核对公式/表格/页码坐标质量；② Qdrant + Qwen3-Embedding 跑通最小检索；③ DeepSeek API 按给定 prompt 模板测引用编号遵循率。
3. **立项目**：确定仓库结构（建议 `backend/`、`frontend/`、`infra/`（docker-compose）、`docs/`），把 02 报告的 FR 编号体系导入 issue 跟踪，从 FR-KB-01（批量导入）与 FR-DP-01（解析）开工。
4. **实施前重验**：04 报告中所有版本号与 API 价格需在开工当天重新核对官方页面（模型迭代极快）。
