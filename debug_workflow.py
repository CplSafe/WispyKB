#!/usr/bin/env python3
"""调试工作流结构"""

import asyncio
import json
import os
import psycopg_pool

async def debug_workflow(workflow_id: str):
    """打印工作流的详细结构"""
    # 初始化数据库连接
    db_host = os.getenv("DB_HOST", "localhost")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME", "ai_kb")
    db_user = os.getenv("DB_USER", "postgres")
    db_password = os.getenv("DB_PASSWORD", "postgres")

    conninfo = f"host={db_host} port={db_port} dbname={db_name} user={db_user} password={db_password}"
    pool = psycopg_pool.AsyncConnectionPool(conninfo, min_size=1, max_size=2)

    async with pool.connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT definition FROM workflows WHERE id = %s", (workflow_id,))
            row = await cur.fetchone()

            if not row:
                print(f"❌ 工作流 {workflow_id} 不存在")
                return

            definition = row[0]

            print("=" * 60)
            print("🔍 工作流结构分析")
            print("=" * 60)

            nodes = definition.get('nodes', [])
            edges = definition.get('edges', [])

            print(f"\n📦 节点数量: {len(nodes)}")
            print(f"🔗 边数量: {len(edges)}")

            print("\n" + "=" * 60)
            print("📋 节点详情")
            print("=" * 60)

            for node in nodes:
                node_id = node.get('id')
                node_type = node.get('type')
                data = node.get('data', {})

                print(f"\n🔹 节点 ID: {node_id}")
                print(f"   类型: {node_type}")
                print(f"   标题: {data.get('title', 'N/A')}")

                if node_type == 'start':
                    outputs = data.get('outputs', {})
                    print(f"   📤 outputs: {json.dumps(outputs, ensure_ascii=False, indent=6)}")

                elif node_type == 'knowledge-base':
                    inputs_values = data.get('inputsValues', {})
                    print(f"   📥 inputsValues:")
                    for key, val in inputs_values.items():
                        print(f"      - {key}: {json.dumps(val, ensure_ascii=False)}")

                elif node_type == 'llm':
                    inputs_values = data.get('inputsValues', {})
                    prompt = inputs_values.get('prompt', {})
                    print(f"   📝 prompt: {json.dumps(prompt, ensure_ascii=False)}")

            print("\n" + "=" * 60)
            print("🔗 边连接")
            print("=" * 60)

            for edge in edges:
                source = edge.get('sourceNodeID') or edge.get('sourceNodeId')
                target = edge.get('targetNodeID') or edge.get('targetNodeId')
                print(f"   {source} → {target}")

    await pool.close()

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python3 debug_workflow.py <workflow_id>")
        sys.exit(1)

    workflow_id = sys.argv[1]
    asyncio.run(debug_workflow(workflow_id))
