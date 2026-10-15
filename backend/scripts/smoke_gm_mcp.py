"""gm-memory MCP server stdio 冒烟测试（任务 #44 验收）。

验证链路：
  MCPClient(StdioMCPConfig) → connect() → list_tools()
  → permission_gate.audit_tools()（权限矩阵，预期全 ALLOW）
  → 真实调用 memory_search / memory_profile（打 GM :8001）
  → close()

用法（backend 目录下）：
  ./venv/Scripts/python.exe scripts/smoke_gm_mcp.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# 读 .env 里的 GM_* 配置（不依赖 pydantic-settings，手工解析即可）
env_file = BACKEND / ".env"
gm_env: dict[str, str] = {}
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip()
        if k in ("GM_BASE_URL", "GM_TOKEN", "GM_USER_ID", "GM_MCP_TIMEOUT_S"):
            gm_env[k] = v

SERVER = BACKEND / "mcp_server" / "gm_memory_server.py"
PYTHON = str(BACKEND / "venv" / "Scripts" / "python.exe")


def _text_of(chunk) -> str:
    """把 ToolChunk.content 拼成可读文本。"""
    parts = []
    for block in getattr(chunk, "content", None) or []:
        if isinstance(block, dict):
            parts.append(str(block.get("text") or block.get("content") or block))
        else:
            parts.append(str(getattr(block, "text", block)))
    return "\n".join(parts)


async def main() -> int:
    from agentscope.mcp import MCPClient, StdioMCPConfig
    from app.tools.permission_gate import audit_tools

    client = MCPClient(
        name="gm-memory",
        is_stateful=True,  # STDIO 必须 stateful
        mcp_config=StdioMCPConfig(
            command=PYTHON,
            args=[str(SERVER)],
            env=gm_env or None,
            cwd=str(BACKEND),
        ),
    )

    print("== 1. connect ==")
    await client.connect()
    print("   OK")

    try:
        print("== 2. list_tools ==")
        tools = await client.list_tools()
        for t in tools:
            print(f"   - {t.name}  (is_read_only={t.is_read_only}, is_mcp={t.is_mcp})")
        assert len(tools) == 3, f"期望 3 个工具，实际 {len(tools)}"

        print("== 3. 权限矩阵（生产同款引擎，DEFAULT 模式） ==")
        rows = await audit_tools(tools)
        for r in rows:
            print(f"   {r['name']:<40} {r['behavior']:<8} read_only={r['is_read_only']}  {r['reason'][:60]}")
        bad = [r for r in rows if r["behavior"] != "allow"]
        assert not bad, f"存在非 ALLOW 工具：{bad}"

        print("== 4. 真实调用 memory_search ==")
        search_tool = next(t for t in tools if t.name.endswith("__memory_search"))
        chunk = await search_tool(query="用户的偏好", limit=3)
        text = _text_of(chunk)
        print(f"   state={getattr(chunk, 'state', None)}")
        print(f"   result={text[:600]}")
        payload = json.loads(text)
        assert payload.get("ok") is True, f"memory_search 失败：{text[:300]}"

        print("== 5. 真实调用 memory_profile ==")
        prof_tool = next(t for t in tools if t.name.endswith("__memory_profile"))
        chunk = await prof_tool(include_inferred=False)
        text = _text_of(chunk)
        print(f"   result={text[:400]}")
        payload = json.loads(text)
        assert payload.get("ok") is True, f"memory_profile 失败：{text[:300]}"

        print("== 6. 错误路径：memory_task_status 传不存在的 id ==")
        task_tool = next(t for t in tools if t.name.endswith("__memory_task_status"))
        chunk = await task_tool(task_id="not-exist-task-id")
        text = _text_of(chunk)
        print(f"   result={text[:300]}")
        # 工具层永不抛异常，GM 404 应被转成 ok=false
        payload = json.loads(text)
        assert payload.get("ok") is False, f"期望 ok=false，实际：{text[:200]}"

        print("\n[SMOKE PASS] 全部 6 步通过")
        return 0
    finally:
        await client.close()
        print("== client closed ==")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
