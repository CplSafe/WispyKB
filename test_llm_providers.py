#!/usr/bin/env python3
"""
LLM 服务提供者测试脚本

测试 Ollama 和 vLLM 的集成是否正常工作

使用方法:
    # 测试 Ollama
    python test_llm_providers.py --provider ollama

    # 测试 vLLM
    python test_llm_providers.py --provider vllm

    # 测试流式输出
    python test_llm_providers.py --provider vllm --stream

    # 自动测试（优先 vLLM）
    python test_llm_providers.py
"""

import asyncio
import sys
import argparse
from services.llm import LLMService, LLMProvider


async def test_ollama():
    """测试 Ollama 服务"""
    print("\n" + "=" * 60)
    print("测试 Ollama 服务")
    print("=" * 60)

    service = LLMService(
        provider="ollama",
        base_url="http://localhost:11434",
        model="qwen2.5:7b"
    )

    # 健康检查
    print("\n[1/3] 健康检查...")
    if service.is_available():
        print("✅ Ollama 服务可用")
    else:
        print("❌ Ollama 服务不可用")
        return False

    # 非流式调用
    print("\n[2/3] 测试非流式调用...")
    messages = [
        {"role": "user", "content": "你好，请用一句话介绍你自己"}
    ]

    try:
        response = await service.chat(messages, temperature=0.7, max_tokens=100)
        print(f"✅ 非流式调用成功")
        print(f"回复: {response}")
    except Exception as e:
        print(f"❌ 非流式调用失败: {e}")
        return False

    # 流式调用
    print("\n[3/3] 测试流式调用...")
    messages = [
        {"role": "user", "content": "请列举3个Python的优点"}
    ]

    try:
        print("回复: ", end="", flush=True)
        token_count = 0
        async for token in service.chat_stream(messages, temperature=0.7, max_tokens=200):
            print(token, end="", flush=True)
            token_count += 1
        print(f"\n✅ 流式调用成功，共 {token_count} 个 token")
    except Exception as e:
        print(f"\n❌ 流式调用失败: {e}")
        return False

    print("\n" + "=" * 60)
    print("✅ Ollama 所有测试通过")
    print("=" * 60)
    return True


async def test_vllm():
    """测试 vLLM 服务"""
    print("\n" + "=" * 60)
    print("测试 vLLM 服务")
    print("=" * 60)

    # 常见的 Qwen 模型名称
    model_names = [
        "Qwen/Qwen2.5-7B-Instruct",
        "Qwen2.5-7B-Instruct",
        "qwen2.5-7b-instruct",
        "Qwen2-7B-Instruct",
    ]

    service = LLMService(
        provider="vllm",
        base_url="http://localhost:8000",
        model=model_names[0]  # 会使用 vLLM 加载的默认模型
    )

    # 健康检查
    print("\n[1/3] 健康检查...")
    if service.is_available():
        print("✅ vLLM 服务可用")
    else:
        print("❌ vLLM 服务不可用")
        print("提示: 请确保 vLLM 服务已启动")
        print("启动命令示例: vllm serve Qwen/Qwen2.5-7B-Instruct --port 8000")
        return False

    # 非流式调用
    print("\n[2/3] 测试非流式调用...")
    messages = [
        {"role": "user", "content": "你好，请用一句话介绍你自己"}
    ]

    try:
        response = await service.chat(messages, temperature=0.7, max_tokens=100)
        print(f"✅ 非流式调用成功")
        print(f"回复: {response}")
    except Exception as e:
        print(f"❌ 非流式调用失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    # 流式调用
    print("\n[3/3] 测试流式调用...")
    messages = [
        {"role": "user", "content": "请列举3个Python的优点"}
    ]

    try:
        print("回复: ", end="", flush=True)
        token_count = 0
        async for token in service.chat_stream(messages, temperature=0.7, max_tokens=200):
            print(token, end="", flush=True)
            token_count += 1
        print(f"\n✅ 流式调用成功，共 {token_count} 个 token")
    except Exception as e:
        print(f"\n❌ 流式调用失败: {e}")
        import traceback
        traceback.print_exc()
        return False

    print("\n" + "=" * 60)
    print("✅ vLLM 所有测试通过")
    print("=" * 60)
    return True


async def main():
    parser = argparse.ArgumentParser(description="测试 LLM 服务提供者")
    parser.add_argument(
        "--provider",
        choices=["ollama", "vllm", "auto", "all"],
        default="auto",
        help="选择要测试的服务提供者"
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="测试流式输出"
    )

    args = parser.parse_args()

    if args.provider == "all":
        print("测试所有服务提供者...")
        results = {
            "Ollama": await test_ollama(),
            "vLLM": await test_vllm()
        }

        print("\n" + "=" * 60)
        print("测试结果汇总")
        print("=" * 60)
        for provider, success in results.items():
            status = "✅ 通过" if success else "❌ 失败"
            print(f"{provider}: {status}")

    elif args.provider == "auto":
        # 自动检测：优先 vLLM，回退到 Ollama
        print("自动检测服务提供者（优先 vLLM）...")

        vllm_success = await test_vllm()
        if not vllm_success:
            print("\nvLLM 不可用，尝试 Ollama...")
            await test_ollama()

    elif args.provider == "ollama":
        await test_ollama()

    elif args.provider == "vllm":
        await test_vllm()


if __name__ == "__main__":
    asyncio.run(main())
