# -*- coding: utf-8 -*-
"""C 步实测：GM v2.6.0 Search 的 agent_id / source_system 过滤与跨 Agent 共享。

只读为主；唯一写入是一条带 agent_id="test_agent_b" 的测试记忆（用 ai_assistant
凭证，source_system 仍为 ai_assistant），用于验证过滤维度。测试后按输出决定清理。
"""
import asyncio
import json
import sys
import time
import uuid

import httpx

sys.path.insert(0, ".")
from app.config import settings  # noqa: E402

BASE = settings.gm_base_url
HDRS = {
    "Authorization": f"Bearer {settings.gm_token.strip()}",
    "Content-Type": "application/json",
}
USER = settings.gm_user_id
QUERY = "测试代理的蓝色代码是什么"
TEMPLATE = (
    "请记住：{text}"
    "（范围说明：这是用户本人的信息。若内容未注明特定业务场景或项目限定，"
    "视为通用个人信息；若内容本身写明场景或项目限定条件，按该限定标注，不要泛化。）"
)


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, trust_env=False)


async def search(client, query, *, agent_id=None, source_system=None, limit=5):
    body = {"user_id": USER, "query": query, "limit": limit, "use_model": False}
    if agent_id is not None:
        body["agent_id"] = agent_id
    if source_system is not None:
        body["source_system"] = source_system
    r = await client.post(f"{BASE}/api/v1/memories:search", headers=HDRS, json=body)
    if r.status_code != 200:
        return {"http": r.status_code, "body": r.text[:200]}
    return [x["memory"][:40] for x in r.json()["results"]]


async def main():
    tag = uuid.uuid4().hex[:6].upper()
    fact = f"测试代理B的专属蓝色代码是 ORCA-{tag}"
    session_id = f"cstep-agentid-{tag.lower()}"

    async with _client() as client:
        print("=" * 60)
        print("[0] 基线：既有记忆（agent_id 全为 ai_assistant）")
        r_base = await search(client, "用户的工号是什么")
        print("  no-filter      :", r_base[:3])
        r_a = await search(client, "用户的工号是什么", agent_id="ai_assistant")
        print("  agent=ai_asst  :", r_a[:3])
        r_b = await search(client, "用户的工号是什么", agent_id="no_such_agent")
        print("  agent=no_such  :", r_b)
        r_c = await search(client, "用户的工号是什么", source_system="ai_assistant")
        print("  src=ai_asst    :", r_c[:3])
        r_d = await search(client, "用户的工号是什么", source_system="email_agent")
        print("  src=email_asst :", r_d)

        print("=" * 60)
        print(f"[1] 注入测试记忆：agent_id=test_agent_b，事实={fact!r}")
        msg = {
            "message_id": uuid.uuid4().hex,
            "role": "user",
            "content": TEMPLATE.format(text=fact),
            "occurred_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        }
        payload = {
            "agent_id": "test_agent_b",
            "source_system": settings.gm_source_system,
            "user_id": USER,
            "session_id": session_id,
            "messages": [msg],
            "wait_for_result": True,
            "wait_timeout_ms": 20000,
        }
        hdrs = dict(HDRS, **{"Idempotency-Key": f"cstep-{tag.lower()}"})
        r = await client.post(f"{BASE}/api/v1/memories:add", headers=hdrs, json=payload)
        print("  add http:", r.status_code)
        task = r.json()
        print("  task:", task.get("task_id"), "status:", task.get("status"))
        results = task.get("results") or []
        for item in results:
            print("   result:", json.dumps(item, ensure_ascii=False)[:160])
        saved_ids = [mid for item in results for mid in item.get("memory_ids", [])]
        print("  saved memory_ids:", saved_ids)

        # 若 wait 后仍 pending，轮询到终态
        if task.get("status") in ("pending", "processing"):
            for _ in range(15):
                await asyncio.sleep(2)
                r2 = await client.get(f"{BASE}/api/v1/tasks/{task['task_id']}", headers=HDRS)
                task = r2.json()
                if task.get("status") not in ("pending", "processing"):
                    break
            print("  final task status:", task.get("status"))
            results = task.get("results") or []
            saved_ids = [mid for item in results for mid in item.get("memory_ids", [])]
            print("  final saved memory_ids:", saved_ids)

        print("=" * 60)
        print("[2] 检索验证（query 直指测试事实）")
        cases = [
            ("no-filter（跨 Agent 共享视角）", dict()),
            ("agent_id=test_agent_b（本 Agent）", dict(agent_id="test_agent_b")),
            ("agent_id=ai_assistant（他 Agent）", dict(agent_id="ai_assistant")),
            ("agent_id=no_such_agent", dict(agent_id="no_such_agent")),
            ("source_system=ai_assistant（注入时同凭证）", dict(source_system="ai_assistant")),
            ("source_system=email_agent", dict(source_system="email_agent")),
            ("agent_id=test_agent_b + source_system=ai_assistant",
             dict(agent_id="test_agent_b", source_system="ai_assistant")),
        ]
        for name, kw in cases:
            hits = await search(client, QUERY, **kw)
            mark = any(f"ORCA-{tag}" in h for h in hits) if isinstance(hits, list) else "?"
            print(f"  [{'HIT ' if mark else 'MISS'}] {name}: {hits if not isinstance(hits, dict) else hits}")

        print("=" * 60)
        print("[3] 回归：注入后既有记忆检索不受影响")
        r_after = await search(client, "用户的工号是什么")
        print("  no-filter      :", r_after[:3])
        r_after_a = await search(client, "用户的工号是什么", agent_id="ai_assistant")
        print("  agent=ai_asst  :", r_after_a[:3])

        print("=" * 60)
        print("TEST_TAG=", tag, " SESSION=", session_id)


asyncio.run(main())
