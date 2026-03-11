#!/usr/bin/env python3
"""测试 Milvus 连接"""

import asyncio
from pymilvus import connections, utility

async def test_milvus():
    try:
        # 连接到 Milvus
        connections.connect(
            alias="default",
            host="localhost",
            port="19530"
        )
        print("✅ 成功连接到 Milvus")

        # 列出所有集合
        collections = utility.list_collections()
        print(f"📚 已有集合数量: {len(collections)}")
        if collections:
            print(f"   集合列表: {', '.join(collections)}")
        else:
            print("   (暂无集合)")

        # 断开连接
        connections.disconnect("default")
        print("✅ 测试完成")

    except Exception as e:
        print(f"❌ 连接失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_milvus())
