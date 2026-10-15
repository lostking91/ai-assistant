"""gm-memory MCP server：把 GeneralMemory v2.6.0 的只读能力封装为标准 MCP 工具。

形态：stdio 子进程（FastMCP），由 ai-assistant 后端经 AgentScope MCPClient
（StdioMCPConfig）拉起，生命周期随宿主进程——GM 侧零改动、不占新端口。

设计边界（有意为之）：
- 只暴露**只读**工具（search / task / profile）。写入仍走后端既有链路
  （save_memory 工具 + outbox 回合钩子），原因：
  1. MCPTool 是"每次调用临时构造"（Toolkit._get_available_tools → client.list_tools()），
     宿主侧无法注入会话闭包，MCP add 工具拿不到 session_id；
  2. 写侧需要 outbox 落库、幂等键、终态轮询等本地事务语义，MCP server 无法访问
     后端 SQLite，绕开 outbox 直写 GM 会丢失可观测性与重试保障。
- 全部工具标注 readOnlyHint=True：AgentScope MCPTool.check_permissions 对只读
  工具返回 ALLOW（且引擎 Step3 只读快速通道兜底），保证 DEFAULT 权限模式下
  不会被判 ASK 而无声卡死（v1.1.1 教训）。注册侧仍会过 permission_gate 复核。
- GM 契约与 app/memory_client.py、app/memory_retriever.py 保持一致：
  * httpx trust_env=False（绕过环境代理，本地回环调用必须）；
  * search 用 use_model=false（纯向量检索，v2.6.0 提交 031ea68）；
  * 日志只写 stderr（stdout 是 MCP 协议通道，写入会破坏 JSON-RPC 帧）。

运行：python mcp_server/gm_memory_server.py   （环境变量 GM_BASE_URL/GM_TOKEN/GM_USER_ID）
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# ---------------------------------------------------------------- 配置（与后端 .env 同名字段）

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_GM_BASE_URL = os.environ.get("GM_BASE_URL", "http://127.0.0.1:8001").rstrip("/")
_GM_TOKEN = os.environ.get("GM_TOKEN", "")
_GM_USER_ID = os.environ.get("GM_USER_ID", "gm_user_lw")
_TIMEOUT_S = float(os.environ.get("GM_MCP_TIMEOUT_S", "10"))

# stdout 是 MCP 协议通道，日志必须走 stderr
logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                    format="%(asctime)s | %(levelname)-7s | gm-mcp | %(message)s")
logger = logging.getLogger("gm_memory_server")

_READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

mcp = FastMCP("gm-memory")


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if _GM_TOKEN.strip():
        headers["Authorization"] = f"Bearer {_GM_TOKEN.strip()}"
    return headers


def _error_text(detail: str) -> str:
    """统一的错误返回文本（工具层永不抛异常，保证模型拿到可读结论）。"""
    return json.dumps({"ok": False, "error": detail}, ensure_ascii=False)


# ---------------------------------------------------------------- 工具 1：记忆检索


@mcp.tool(
    name="memory_search",
    description=(
        "检索用户的长期记忆（GeneralMemory 向量语义检索，use_model=false）。"
        "返回与 query 语义相关的记忆列表（内容+认知类型）。只读操作。"
    ),
    annotations=_READ_ONLY,
)
async def memory_search(query: str, limit: int = 5) -> str:
    """POST /api/v1/memories:search 的 MCP 封装。

    Args:
        query: 检索文本（自然语言）。
        limit: 最多返回条数，1-20。
    """
    query = (query or "").strip()
    if not query:
        return _error_text("query 不能为空")
    limit = max(1, min(int(limit or 5), 20))
    body = {
        "user_id": _GM_USER_ID,
        "query": query,
        "limit": limit,
        "use_model": False,
    }
    try:
        # trust_env=False：绕过环境代理（同 memory_retriever，本地回环必须）
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, trust_env=False) as client:
            resp = await client.post(
                f"{_GM_BASE_URL}/api/v1/memories:search", json=body, headers=_headers()
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        return _error_text(f"GM 返回 {exc.response.status_code}：{exc.response.text[:200]}")
    except httpx.HTTPError as exc:
        return _error_text(f"GM 服务不可达：{type(exc).__name__}: {exc}")

    results = [
        {
            "id": str(row.get("id") or ""),
            "memory": str(row.get("memory") or ""),
            "cognitive_type": str(row.get("cognitive_type") or ""),
        }
        for row in (data.get("results") or [])
        if isinstance(row, dict) and str(row.get("memory") or "").strip()
    ]
    return json.dumps(
        {"ok": True, "count": len(results), "results": results}, ensure_ascii=False
    )


# ---------------------------------------------------------------- 工具 2：任务状态查询


@mcp.tool(
    name="memory_task_status",
    description="按 task_id 查询 GeneralMemory 记忆写入任务的执行状态（pending/processing/succeeded/failed 等）。只读操作。",
    annotations=_READ_ONLY,
)
async def memory_task_status(task_id: str) -> str:
    """GET /api/v1/tasks/{task_id} 的 MCP 封装。

    Args:
        task_id: GM 任务 ID（save_memory / add 返回的 task_id）。
    """
    task_id = (task_id or "").strip()
    if not task_id:
        return _error_text("task_id 不能为空")
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, trust_env=False) as client:
            resp = await client.get(
                f"{_GM_BASE_URL}/api/v1/tasks/{task_id}", headers=_headers()
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        return _error_text(f"GM 返回 {exc.response.status_code}：{exc.response.text[:200]}")
    except httpx.HTTPError as exc:
        return _error_text(f"GM 服务不可达：{type(exc).__name__}: {exc}")

    # 只透传模型关心的字段，避免整包任务对象撑爆上下文
    summary: dict[str, Any] = {
        "ok": True,
        "task_id": data.get("task_id") or data.get("id"),
        "status": data.get("status"),
        "created_at": data.get("created_at"),
        "finished_at": data.get("finished_at"),
    }
    results = data.get("results")
    if isinstance(results, list):
        summary["results_count"] = len(results)
        summary["results"] = [
            {
                "id": str(r.get("id") or ""),
                "memory": str(r.get("memory") or ""),
            }
            for r in results
            if isinstance(r, dict)
        ][:10]
    error = data.get("error") or data.get("failure_reason")
    if error:
        summary["error"] = str(error)[:300]
    return json.dumps(summary, ensure_ascii=False)


# ---------------------------------------------------------------- 工具 3：用户画像查询


@mcp.tool(
    name="memory_profile",
    description="查询 GeneralMemory 为用户沉淀的画像（profile，含已确认与推断条目）。只读操作。",
    annotations=_READ_ONLY,
)
async def memory_profile(include_inferred: bool = False) -> str:
    """GET /api/v1/profiles 的 MCP 封装。

    Args:
        include_inferred: 是否包含推断（未确认）画像条目，默认 false。
    """
    params = {"user_id": _GM_USER_ID, "include_inferred": str(bool(include_inferred)).lower()}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, trust_env=False) as client:
            resp = await client.get(
                f"{_GM_BASE_URL}/api/v1/profiles", params=params, headers=_headers()
            )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        return _error_text(f"GM 返回 {exc.response.status_code}：{exc.response.text[:200]}")
    except httpx.HTTPError as exc:
        return _error_text(f"GM 服务不可达：{type(exc).__name__}: {exc}")

    return json.dumps({"ok": True, "user_id": _GM_USER_ID, "profile": data},
                      ensure_ascii=False)[:6000]


# ---------------------------------------------------------------- 入口


if __name__ == "__main__":
    logger.info(
        "gm-memory MCP server 启动：GM=%s user=%s token=%s",
        _GM_BASE_URL, _GM_USER_ID, "已配置" if _GM_TOKEN.strip() else "未配置",
    )
    mcp.run(transport="stdio")
