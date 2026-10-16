"""Cortex 后端入口。

启动：uvicorn app.main:app --port 8300 （在 backend/ 目录下）
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db
from .routers.api import router

app = FastAPI(title="Cortex — 个人知识库问答 Agent", version="0.1.0")

# 前端开发服务器跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)

# 幂等初始化：建表/默认行。导入期执行，避免依赖 lifespan 触发时机。
init_db()
