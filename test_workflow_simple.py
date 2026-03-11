#!/usr/bin/env python3
"""简单的工作流测试脚本"""

import asyncio
import httpx
import json

async def test_workflow():
    workflow_id = "cab5eaec-3262-458d-be07-b3d143e1af1b"

    # 登录获取 token
    async with httpx.AsyncClient() as client:
        login_resp = await client.post(
            "http://localhost:8888/api/v1/auth/login",
            json={"username": "admin", "password": "admin123"}
        )
        token = login_resp.json()["access_token"]

        print("✅ 登录成功")

        # 执行工作流
        headers = {"Authorization": f"Bearer {token}"}

        print("\n📤 发送测试输入: query='如何使用 Python 读取 CSV 文件？'")

        exec_resp = await client.post(
            f"http://localhost:8888/api/v1/workflows/{workflow_id}/execute",
            json={"query": "如何使用 Python 读取 CSV 文件？"},
            headers=headers,
            timeout=60.0
        )

        print(f"\n📥 响应状态: {exec_resp.status_code}")

        if exec_resp.status_code == 200:
            result = exec_resp.json()
            print("\n✅ 工作流执行成功!")
            print(f"\n结果预览:")
            print(json.dumps(result, ensure_ascii=False, indent=2)[:500])
        else:
            print(f"\n❌ 工作流执行失败:")
            print(exec_resp.text)

if __name__ == "__main__":
    asyncio.run(test_workflow())
