"""gm-memory MCP 连接管理器（模块级共享，随 FastAPI lifespan 启停）。

为什么共享连接而不是每轮 build_toolkit 时新起子进程：
  - STDIO MCP 必须 stateful（MCPClient.model_post_init 强制），每次 connect
    都会 spawn 一个 python 子进程并做 initialize 握手（实测 ~0.1s，但进程
    创建/销毁开销与句柄泄漏风险随轮次线性累积）；
  - AgentScope 的 MCPTool（stateful 模式）绑定的是 client 的 ClientSession，
    session 基于 JSON-RPC id 多路复用，支持并发调用，可安全跨轮共享；
  - Toolkit.add_tool 接受 MCPTool 实例直接注册，工具调用走 session.call_tool，
    不依赖 Toolkit 每轮重拉（那是 mcps=[...] 挂载方式的语义）。

生命周期：
  - startup_mcp()：lifespan 启动时调用；连接失败仅告警降级（MCP 工具缺席，
    会话与其它工具不受影响），不阻断服务启动；
  - get_mcp_tools()：build_toolkit 每轮调用；返回缓存的 MCPTool 列表。
    若连接已断（子进程崩溃等），带锁尝试重连一次，仍失败则返回 []；
  - shutdown_mcp()：lifespan 退出时关闭连接、回收子进程。

工具过滤：settings.mcp_gm_memory_tools（逗号分隔的 server 侧原始工具名，
空=全部），直接映射到 MCPClient.enable_tools。
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = BACKEND_DIR / "mcp_server" / "gm_memory_server.py"

_client = None  # type: ignore[var-annotated]  # MCPClient | None
_tools: list = []  # 缓存的 MCPTool 实例（绑定共享 session）
_lock = asyncio.Lock()
_start_failed = False  # 启动期连接失败则不再反复重试 spawn（避免每轮卡超时）


def mcp_enabled() -> bool:
    """MCP 记忆工具是否应启用（配置开关 + server 脚本存在 + GM 已启用）。"""
    return bool(
        settings.mcp_gm_memory_enabled
        and settings.memory_enabled
        and SERVER_SCRIPT.exists()
    )


def mcp_tools_ready() -> bool:
    """MCP 工具当前是否真实可用（已连接且缓存了工具）。

    供 system_prompt 组装等同步场景使用：连接失败降级时 prompt 不再
    介绍不存在的工具，避免模型调用空工具名。
    """
    return bool(_client is not None and _client.is_connected and _tools)


def _build_client():
    """按当前 settings 构造 MCPClient（不连接）。"""
    from agentscope.mcp import MCPClient, StdioMCPConfig

    enable = [
        t.strip()
        for t in (settings.mcp_gm_memory_tools or "").split(",")
        if t.strip()
    ] or None

    return MCPClient(
        name="gm-memory",  # 工具最终名：mcp__gm-memory__memory_search 等
        is_stateful=True,  # STDIO 强制 stateful
        mcp_config=StdioMCPConfig(
            command=sys.executable,  # 后端自身的 venv python
            args=[str(SERVER_SCRIPT)],
            # MCP SDK 会把 get_default_environment()（PATH/SYSTEMROOT 等）
            # 与此处 env 合并，只需传 GM 相关变量
            env={
                "GM_BASE_URL": settings.gm_base_url,
                "GM_TOKEN": settings.gm_token,
                "GM_USER_ID": settings.gm_user_id,
                "GM_MCP_TIMEOUT_S": str(settings.mcp_gm_memory_timeout_s),
            },
            cwd=str(BACKEND_DIR),
        ),
        enable_tools=enable,
        execution_timeout=settings.mcp_gm_memory_timeout_s,
    )


async def _connect_locked() -> list:
    """（须持有 _lock）建立连接并缓存工具列表；失败抛异常由调用方处置。"""
    global _client, _tools
    client = _build_client()
    await client.connect()
    tools = await client.list_tools()
    if not tools:
        await client.close()
        raise RuntimeError("gm-memory MCP server 未暴露任何工具")
    _client = client
    _tools = tools
    logger.info(
        "gm-memory MCP 已连接：%s", ", ".join(t.name for t in tools)
    )
    return tools


async def startup_mcp() -> None:
    """lifespan 启动钩子：连接 MCP server；失败降级不阻断服务。"""
    global _start_failed
    if not mcp_enabled():
        logger.info("gm-memory MCP 未启用（开关关闭或 server 脚本缺失）")
        return
    try:
        async with _lock:
            await _connect_locked()
    except Exception as exc:  # noqa: BLE001 - 启动降级
        _start_failed = True
        logger.warning(
            "gm-memory MCP 连接失败（降级：本轮服务无 MCP 记忆工具）：%s: %s",
            type(exc).__name__, exc,
        )


async def get_mcp_tools() -> list:
    """返回共享的 MCPTool 列表；连接断开时带锁重连一次，仍失败返回 []。"""
    global _start_failed
    if not mcp_enabled() or _start_failed:
        return []
    if _client is not None and _client.is_connected and _tools:
        return _tools
    # 连接丢失（子进程崩溃等）：重连一次
    async with _lock:
        if _client is not None and _client.is_connected and _tools:
            return _tools  # 双检：别的协程已重连
        try:
            if _client is not None:
                await _client.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            return await _connect_locked()
        except Exception as exc:  # noqa: BLE001 - 运行期降级
            logger.warning(
                "gm-memory MCP 重连失败（本轮无 MCP 记忆工具）：%s: %s",
                type(exc).__name__, exc,
            )
            return []


async def shutdown_mcp() -> None:
    """lifespan 退出钩子：关闭连接、回收 stdio 子进程。"""
    global _client, _tools
    async with _lock:
        if _client is not None:
            try:
                await _client.close()
            except Exception as exc:  # noqa: BLE001
                logger.warning("关闭 gm-memory MCP 出错：%s", exc)
        _client = None
        _tools = []
