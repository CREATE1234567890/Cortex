"""Cortex 后端配置。

所有路径与模型可通过环境变量覆盖；LLM 连接参数存 SQLite settings 表（设置页可改），
env var 仅作首次默认值。
"""
import os
from pathlib import Path

# huggingface_hub 在 import 时读取该端点，必须在任何 hf 相关 import 之前设置。
# 国内网络直连 huggingface.co 不通，默认走镜像。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

# 项目根目录：backend/
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("CORTEX_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "cortex.db"
VECTOR_DIR = DATA_DIR / "vectors"
VECTOR_DIR.mkdir(exist_ok=True)
UPLOAD_DIR = DATA_DIR / "pdfs"
UPLOAD_DIR.mkdir(exist_ok=True)

# 个人知识库根目录（论文文件夹）。可被设置表覆盖。
# 默认指向用户桌面上的论文库；若路径不存在，启动后可在设置页修改。
DEFAULT_KB_ROOT = r"C:\Users\yang'jia'cai\Desktop\论文文件夹"

# --- 嵌入模型 ---
# intfloat/multilingual-e5-small: 检索调优、中英混合、118MB，CPU 友好。
# 需 query:/passage: 前缀（在 embedding.py 内处理）。
# 优先使用本地已下载副本，避免运行时联网。
_LOCAL_EMBED_DIR = DATA_DIR / "models" / "multilingual-e5-small"
EMBED_MODEL = os.getenv(
    "CORTEX_EMBED_MODEL",
    str(_LOCAL_EMBED_DIR) if _LOCAL_EMBED_DIR.exists() else "intfloat/multilingual-e5-small",
)
EMBED_DIM = 384
EMBED_BATCH = 64

# --- 检索参数 ---
DENSE_TOPK = 24        # 稠密召回
BM25_TOPK = 24         # 关键词召回
RRF_K = 60             # RRF 融合常数
FINAL_TOPK = 8         # 进入上下文的证据块数

# --- 切块参数 ---
CHUNK_TARGET_TOKENS = 500
CHUNK_MAX_TOKENS = 700
CHUNK_OVERLAP_RATIO = 0.15

# --- LLM 默认值（可被 settings 表覆盖）---
DEFAULT_LLM_BASE_URL = "https://api.deepseek.com"
DEFAULT_LLM_MODEL = "deepseek-chat"
DEFAULT_LLM_API_KEY = ""

# Chat 上下文里携带的历史轮数
HISTORY_TURNS = 6


def get_kb_root() -> str:
    from .db import get_setting
    return get_setting("kb_root", DEFAULT_KB_ROOT)
