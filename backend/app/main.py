"""FastAPI 服务入口。

接口一览：
  GET  /api/health                 健康检查（含 API Key 配置状态、工具列表）
  POST /api/chat                   非流式对话
  POST /api/chat/stream            流式对话（SSE）
  GET  /api/sessions               会话列表（按最近更新排序）
  GET  /api/sessions/{sid}/history 某会话的历史消息
  PATCH /api/sessions/{sid}        重命名会话
  DELETE /api/sessions/{sid}       删除会话
  GET  /api/memory/link/blocked    已阻断待轮换的记忆链路会话列表
  GET  /api/memory/link/{sid}      某会话的记忆链路状态（GM 会话代次）
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import agent as agent_service
from . import database as db
from . import memory_link
from .config import settings
from .models import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SessionHistory,
    SessionItem,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
logger = logging.getLogger("ai-assistant")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await db.init_db()
    agent_service.check_api_key()
    logger.info("数据库就绪：%s", settings.database_path)
    logger.info("服务监听：%s:%s", settings.host, settings.port)
    # 启动时补投上次未完成的记忆发件（幂等键重发，GM 侧去重）
    if agent_service.memory_client.memory_enabled():
        import asyncio

        asyncio.create_task(agent_service.memory_client.flush_outbox())
        logger.info("记忆服务已启用：%s", settings.gm_base_url)
    else:
        logger.info("记忆服务未启用（MEMORY_ENABLED=false 或 GM 配置缺失）")
    yield


app = FastAPI(
    title="AI 助手服务",
    description="基于 AgentScope 2.x 的对话智能体服务",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------ 健康检查


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    try:
        tools = await agent_service.list_tools()
    except Exception:  # noqa: BLE001
        tools = []
    return HealthResponse(
        status="ok",
        model=settings.model_name,
        api_key_configured=settings.has_api_key,
        tools=tools,
    )


# ------------------------------------------------------------------ 非流式对话


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    try:
        result = await agent_service.chat(
            message=req.message,
            session_id=req.session_id,
            resume=req.resume,
        )
    except agent_service.AssistantError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return ChatResponse(**result)


# ------------------------------------------------------------------ 流式对话


def _sse(event: dict[str, Any]) -> str:
    """把事件字典序列化为 SSE 报文。"""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.post("/api/chat/stream")
async def chat_stream(req: ChatRequest) -> StreamingResponse:
    async def event_source() -> AsyncGenerator[str, None]:
        async for event in agent_service.chat_stream(
            message=req.message,
            session_id=req.session_id,
            resume=req.resume,
        ):
            yield _sse(event)
        yield "event: end\ndata: {}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------------------------------------------ 会话管理


@app.get("/api/sessions", response_model=list[SessionItem])
async def sessions(limit: int = 50) -> list[SessionItem]:
    rows = await db.list_sessions(limit=min(max(limit, 1), 200))
    return [SessionItem(**row) for row in rows]


class RenameRequest(BaseModel):
    title: str


@app.patch("/api/sessions/{session_id}")
async def rename(session_id: str, req: RenameRequest) -> dict[str, str]:
    row = await db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    title = req.title.strip() or "新对话"
    await db.rename_session(session_id, title)
    return {"session_id": session_id, "title": title}


@app.delete("/api/sessions/{session_id}")
async def remove(session_id: str) -> dict[str, str]:
    row = await db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    await db.delete_session(session_id)
    return {"status": "deleted", "session_id": session_id}


@app.get("/api/sessions/{session_id}/history", response_model=SessionHistory)
async def history(session_id: str) -> SessionHistory:
    row = await db.get_session(session_id)
    if not row:
        raise HTTPException(status_code=404, detail="会话不存在")
    title, messages = await agent_service.extract_history(session_id)
    return SessionHistory(session_id=session_id, title=title, messages=messages)


# ------------------------------------------------------------------ 记忆链路观测

# 说明：这两个端点用于观测 GM 会话级"顺序阻断"兼容机制（见 memory_link.py）的
# 运行状态——本地会话当前映射到第几代 GM 会话、是否被标记阻断、有哪些会话待轮换。
# 路由 /api/memory/link/blocked 必须定义在 /api/memory/link/{session_id} 之前，
# 否则字面量 "blocked" 会被当作 session_id 吞掉。


@app.get("/api/memory/link/blocked")
async def memory_link_blocked() -> dict[str, Any]:
    """列出所有已标记阻断、待轮换的本地会话（观测轮换机制健康度）。"""
    rows = await memory_link.list_blocked()
    return {"count": len(rows), "blocked_sessions": rows}


@app.get("/api/memory/link/{session_id}")
async def memory_link_status(session_id: str) -> dict[str, Any]:
    """查询某本地会话的记忆链路状态：当前 GM 会话、代次、是否阻断。"""
    link = await memory_link.link_status(session_id)
    if link is None:
        return {
            "session_id": session_id,
            "gm_session_id": None,
            "generation": 0,
            "blocked": False,
            "note": "该会话尚未建立记忆链路映射（可能还没触发过记忆写入）。",
        }
    return link


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=True)
