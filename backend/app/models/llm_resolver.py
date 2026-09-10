"""按请求解析 LLM 实例（从 ``api/chat.py`` 抽出的中立模块）。

为什么单独成模块：知识图谱抽取在 ``storage/graph_store.py`` 与
``pipeline/graph/worker.py`` 里需要「按 model_config_id 取一个 LLM 实例」的能力。
该能力原先定义在 ``app/api/chat.py`` 中，而对话链路在本产品线已被删除；
图谱代码按「配置关闭、模块保留」的策略保留下来，因此必须把这份解析逻辑搬到
中立位置，否则图谱开关一旦打开就会 ImportError。

优先级与上游一致：指定 ``model_config_id`` > 数据库中的默认配置 > 系统全局配置。
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.models.llm.ollama import OllamaLLM
from app.models.llm.vllm import VllmLLM
from app.models.provider import LLMProvider
from app.schema.db import LLMConfig
from app.storage.database import async_session

logger = logging.getLogger(__name__)

# 进程内 LLM 实例缓存：复用底层 httpx.AsyncClient 连接池，避免每个请求新建客户端
# 却从不关闭导致的连接/文件描述符泄漏（高并发下会耗尽 FD）。
# httpx.AsyncClient 绑定创建它的事件循环；生产是单循环长驻进程，缓存长期复用即可。
# 测试常为每个用例新建事件循环，故按「当前运行循环」缓存，循环切换时整体重建，
# 避免「Future attached to a different loop」错误。
_llm_cache: dict[tuple, LLMProvider] = {}
_llm_cache_loop: "asyncio.AbstractEventLoop | None" = None


def get_cached_llm(
    provider: str,
    base_url: str,
    model: str,
    api_key: str,
    vendor: str | None = None,
    thinking_control: str | None = None,
    max_output_tokens: int | None = None,
) -> LLMProvider:
    """按 (provider, base_url, model, api_key, vendor, thinking_control) 复用 LLM 实例。

    同一配置返回同一实例（复用 httpx 连接池）；配置变更（改地址/密钥/厂商/思考格式）
    自然命中新 key 生成新实例。
    """
    global _llm_cache, _llm_cache_loop
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not _llm_cache_loop:
        _llm_cache = {}
        _llm_cache_loop = loop

    key = (provider, base_url, model, api_key, vendor, thinking_control, max_output_tokens)
    inst = _llm_cache.get(key)
    if inst is None:
        if provider == "ollama":
            inst = OllamaLLM(base_url=base_url, model=model)
        else:
            inst = VllmLLM(
                base_url=base_url,
                model=model,
                api_key=api_key,
                provider=vendor,
                thinking_control=thinking_control,
                max_output_tokens=max_output_tokens,
            )
        _llm_cache[key] = inst
    return inst


def create_llm_from_config(config: LLMConfig) -> LLMProvider:
    """根据数据库配置创建（或复用）LLM 实例。"""
    max_output_tokens = config.max_output_tokens or get_settings().llm_max_output_tokens
    if config.provider == "ollama":
        return get_cached_llm(
            "ollama", config.base_url, config.model, "",
            max_output_tokens=max_output_tokens,
        )
    # provider 承载基础设施类型（ollama/vllm）；vendor 承载实际模型厂商，
    # thinking_control 承载思考开关的写入格式。
    return get_cached_llm(
        "vllm",
        config.base_url,
        config.model,
        config.api_key or "",
        vendor=config.vendor,
        thinking_control=config.thinking_control,
        max_output_tokens=max_output_tokens,
    )


async def get_llm_for_request(
    model_config_id: str | None,
) -> tuple[LLMProvider, bool, int | None]:
    """根据 ``model_config_id`` 获取 LLM 实例与配置。

    Returns:
        ``(LLM 实例, 是否启用流式, 最大上下文 token 数)``

    注：思考开关（深度思考）由智能体预设独占控制，不来自模型配置。
    """
    if model_config_id:
        async with async_session() as session:
            result = await session.execute(
                select(LLMConfig).where(LLMConfig.id == model_config_id)
            )
            config = result.scalar_one_or_none()
            if config:
                return (
                    create_llm_from_config(config),
                    config.stream_enabled,
                    config.max_context_tokens,
                )

    # 数据库里标记为默认的配置
    async with async_session() as session:
        result = await session.execute(
            select(LLMConfig).where(LLMConfig.is_default == True)  # noqa: E712
        )
        config = result.scalar_one_or_none()
        if config:
            return (
                create_llm_from_config(config),
                config.stream_enabled,
                config.max_context_tokens,
            )

    # 回退到系统全局配置
    settings = get_settings()
    if settings.llm_provider == "vllm":
        return (
            get_cached_llm(
                "vllm", settings.llm_base_url, settings.llm_model, settings.llm_api_key
            ),
            True,
            None,
        )
    return (
        get_cached_llm("ollama", settings.llm_base_url, settings.llm_model, ""),
        True,
        None,
    )
