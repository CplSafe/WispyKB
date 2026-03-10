# LLMService - 大语言模型推理服务
# 统一接口支持 Ollama 和 vLLM（OpenAI-compatible API）

import logging
import os
from typing import List, Dict, Any, Optional, AsyncGenerator
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """LLM 服务提供商"""
    OLLAMA = "ollama"
    VLLM = "vllm"
    OPENAI = "openai"  # 保留，用于未来扩展


class LLMService:
    """
    大语言模型推理服务

    统一接口支持多种推理引擎：
    - Ollama: 本地部署简单，API 格式独特
    - vLLM: 高性能推理引擎，使用 OpenAI-compatible API
    - OpenAI: 云端 API（可选）

    使用示例：
        # Ollama
        service = LLMService(provider="ollama", base_url="http://localhost:11434", model="qwen2.5:7b")

        # vLLM
        service = LLMService(provider="vllm", base_url="http://localhost:8000", model="Qwen/Qwen2.5-7B-Instruct")

        # 调用
        response = await service.chat(messages=[{"role": "user", "content": "你好"}])

        # 流式输出
        async for token in service.chat_stream(messages=[...]):
            print(token, end="")
    """

    def __init__(
        self,
        provider: str = "ollama",
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = 300.0,
    ):
        """
        初始化 LLM 服务

        Args:
            provider: 服务提供商 (ollama/vllm/openai)
            base_url: 服务地址
            model: 模型名称
            api_key: API 密钥（仅 OpenAI 需要）
            timeout: 请求超时时间（秒）
        """
        self.provider = LLMProvider(provider)
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

        # 根据 provider 设置默认 base_url
        if base_url:
            self.base_url = base_url.rstrip('/')
        else:
            if self.provider == LLMProvider.OLLAMA:
                self.base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
            elif self.provider == LLMProvider.VLLM:
                self.base_url = os.getenv("VLLM_BASE_URL", "http://localhost:8000")
            elif self.provider == LLMProvider.OPENAI:
                self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

        logger.info(f"初始化 LLM 服务: provider={self.provider}, base_url={self.base_url}, model={self.model}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        **kwargs
    ) -> str:
        """
        非流式对话

        Args:
            messages: 消息列表，格式：[{"role": "user", "content": "..."}]
            temperature: 温度参数（0-1，越高越随机）
            top_p: nucleus sampling 参数
            max_tokens: 最大生成 token 数
            **kwargs: 其他模型参数

        Returns:
            模型回复文本
        """
        if self.provider == LLMProvider.OLLAMA:
            return await self._chat_ollama(messages, temperature, top_p, max_tokens, **kwargs)
        elif self.provider in [LLMProvider.VLLM, LLMProvider.OPENAI]:
            return await self._chat_openai_compatible(messages, temperature, top_p, max_tokens, **kwargs)
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_tokens: int = 2048,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        流式对话

        Args:
            messages: 消息列表
            temperature: 温度参数
            top_p: nucleus sampling 参数
            max_tokens: 最大生成 token 数
            **kwargs: 其他模型参数

        Yields:
            模型生成的 token
        """
        if self.provider == LLMProvider.OLLAMA:
            async for token in self._chat_stream_ollama(messages, temperature, top_p, max_tokens, **kwargs):
                yield token
        elif self.provider in [LLMProvider.VLLM, LLMProvider.OPENAI]:
            async for token in self._chat_stream_openai_compatible(messages, temperature, top_p, max_tokens, **kwargs):
                yield token
        else:
            raise ValueError(f"不支持的 provider: {self.provider}")

    # ==================== Ollama 实现 ====================

    async def _chat_ollama(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        top_p: float,
        max_tokens: int,
        **kwargs
    ) -> str:
        """Ollama 非流式调用"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "num_ctx": 8192,
                        "temperature": temperature,
                        "top_p": top_p,
                        "num_predict": max_tokens,
                    }
                }

                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()

                # Ollama 返回格式: {"message": {"role": "assistant", "content": "..."}}
                content = data.get("message", {}).get("content", "")
                logger.debug(f"Ollama 调用成功: response_length={len(content)}")
                return content

        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTP 错误: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"Ollama 调用失败: {e}")
            raise

    async def _chat_stream_ollama(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        top_p: float,
        max_tokens: int,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """Ollama 流式调用"""
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "num_ctx": 8192,
                        "temperature": temperature,
                        "top_p": top_p,
                        "num_predict": max_tokens,
                    }
                }

                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout
                ) as response:
                    response.raise_for_status()

                    # Ollama 流式返回格式: 每行一个 JSON 对象
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue

                        try:
                            import json
                            data = json.loads(line)
                            # Ollama 流式格式: {"message": {"content": "..."}, "done": false}
                            content = data.get("message", {}).get("content", "")

                            if content:
                                yield content

                            if data.get("done", False):
                                break

                        except json.JSONDecodeError:
                            logger.warning(f"Ollama 流式解析失败: {line}")
                            continue

        except Exception as e:
            logger.error(f"Ollama 流式调用失败: {e}")
            raise

    # ==================== OpenAI-Compatible API 实现（vLLM） ====================

    async def _chat_openai_compatible(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        top_p: float,
        max_tokens: int,
        **kwargs
    ) -> str:
        """OpenAI-compatible API 非流式调用（vLLM）"""
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_tokens": max_tokens,
                    **kwargs
                }

                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                )
                response.raise_for_status()
                data = response.json()

                # OpenAI 格式: {"choices": [{"message": {"content": "..."}}]}
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                logger.debug(f"OpenAI-compatible API 调用成功: response_length={len(content)}")
                return content

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenAI-compatible API HTTP 错误: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"OpenAI-compatible API 调用失败: {e}")
            raise

    async def _chat_stream_openai_compatible(
        self,
        messages: List[Dict[str, str]],
        temperature: float,
        top_p: float,
        max_tokens: int,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """OpenAI-compatible API 流式调用（vLLM）"""
        try:
            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_tokens": max_tokens,
                    "stream": True,
                    **kwargs
                }

                async with client.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                ) as response:
                    response.raise_for_status()

                    # OpenAI 流式格式: data: {...}
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue

                        # SSE 格式: "data: {...}"
                        if line.startswith("data: "):
                            data_str = line[6:]  # 去掉 "data: " 前缀

                            # 检查是否为结束标记
                            if data_str.strip() == "[DONE]":
                                break

                            try:
                                import json
                                data = json.loads(data_str)
                                # OpenAI 流式格式: {"choices": [{"delta": {"content": "..."}}]}
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")

                                if content:
                                    yield content

                            except json.JSONDecodeError:
                                logger.warning(f"OpenAI-compatible API 流式解析失败: {line}")
                                continue

        except Exception as e:
            logger.error(f"OpenAI-compatible API 流式调用失败: {e}")
            raise

    # ==================== 工具方法 ====================

    def is_available(self) -> bool:
        """检查服务是否可用（简单的健康检查）"""
        import asyncio

        async def check():
            try:
                if self.provider == LLMProvider.OLLAMA:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        response = await client.get(f"{self.base_url}/api/tags")
                        return response.status_code == 200
                elif self.provider in [LLMProvider.VLLM, LLMProvider.OPENAI]:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        # OpenAI-compatible API 没有标准的健康检查端点
                        # 尝试调用 models 端点
                        response = await client.get(f"{self.base_url}/v1/models")
                        return response.status_code == 200
            except Exception as e:
                logger.warning(f"服务健康检查失败: {e}")
                return False

        return asyncio.run(check())

    def get_model_info(self) -> Dict[str, Any]:
        """获取当前模型信息"""
        return {
            "provider": self.provider.value,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": self.timeout,
        }
