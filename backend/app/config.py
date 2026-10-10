"""应用配置：从 .env 读取，避免硬编码密钥。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- DashScope / 通义千问 ----
    dashscope_api_key: str = ""
    model_name: str = "qwen-plus"
    stream_enabled: bool = True

    # ---- 服务 ----
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---- 会话 ----
    session_timeout_minutes: int = 30
    database_path: str = str(BACKEND_DIR / "data" / "assistant.db")

    # ---- 智能体 ----
    agent_name: str = "AI助手"
    max_iters: int = 20

    # ---- GeneralMemory 记忆服务（写入侧） ----
    memory_enabled: bool = True
    gm_base_url: str = "http://127.0.0.1:8001"
    gm_token: str = ""
    gm_source_system: str = "ai_assistant"
    gm_user_id: str = "gm_user_lw"
    gm_wait_timeout_ms: int = 15000

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def has_api_key(self) -> bool:
        key = self.dashscope_api_key.strip()
        return bool(key) and not key.startswith("YOUR_")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
