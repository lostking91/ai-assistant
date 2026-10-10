"""HTTP 接口的请求 / 响应模型。"""

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """一次对话请求。"""

    message: str = Field(..., min_length=1, description="用户输入的消息")
    session_id: str | None = Field(
        default=None, description="会话 ID；为空则新建会话"
    )
    resume: bool = Field(
        default=False,
        description="为 true 时强制续用指定会话（用户主动切换历史会话），忽略闲置超时",
    )


class MessageItem(BaseModel):
    """展示用的单条消息。"""

    role: str = Field(..., description="user / assistant")
    content: str = Field(default="", description="消息文本")
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    created_at: str | None = None


class ChatResponse(BaseModel):
    """非流式对话响应。"""

    session_id: str
    reply: str
    title: str
    is_new_session: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class SessionItem(BaseModel):
    """会话列表项。"""

    session_id: str
    title: str
    created_at: str
    updated_at: str


class SessionHistory(BaseModel):
    """某个会话的历史消息。"""

    session_id: str
    title: str
    messages: list[MessageItem]


class HealthResponse(BaseModel):
    status: str
    model: str
    api_key_configured: bool
    tools: list[str]
