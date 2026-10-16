"""LLM 客户端：OpenAI 兼容协议统一封装（模型路由抽象的最小实现）。

连接参数从 settings 表读取（设置页可改），env 作首次默认。
支持的提供商：DeepSeek / GLM(Zhipu) / 任何 OpenAI 兼容端点 / Ollama。
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from . import config
from .db import get_setting


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str


def get_llm_config() -> LLMConfig:
    return LLMConfig(
        base_url=get_setting("llm_base_url", config.DEFAULT_LLM_BASE_URL).rstrip("/"),
        api_key=get_setting("llm_api_key", config.DEFAULT_LLM_API_KEY),
        model=get_setting("llm_model", config.DEFAULT_LLM_MODEL),
    )


def llm_ready() -> bool:
    cfg = get_llm_config()
    return bool(cfg.api_key and cfg.model)


def chat(messages: list[dict], stream: bool = False, temperature: float = 0.2,
         tools: list[dict] | None = None):
    """返回 OpenAI 客户端的 chat.completions 结果（stream 或完整）。

    tools 非 None 时启用 function calling（Agentic 检索循环用）。
    """
    import re
    from openai import OpenAI
    cfg = get_llm_config()
    base = cfg.base_url
    # 已带版本号的端点（如智谱 /v4、Ollama /api）原样使用；其余补 /v1（DeepSeek 风格）
    if not re.search(r"/v\d+$", base):
        base = f"{base}/v1"
    client = OpenAI(base_url=base, api_key=cfg.api_key or "EMPTY",
                    timeout=120, max_retries=1)
    kwargs = dict(
        model=cfg.model,
        messages=messages,
        stream=stream,
        temperature=temperature,
    )
    if tools:
        kwargs["tools"] = tools
    return client.chat.completions.create(**kwargs)


def chat_text(messages: list[dict], temperature: float = 0.2) -> str:
    """非流式便捷方法，返回纯文本。"""
    resp = chat(messages, stream=False, temperature=temperature)
    return resp.choices[0].message.content or ""
