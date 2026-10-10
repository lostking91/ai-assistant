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

    # ---- GeneralMemory 记忆服务（读取侧 / 本地向量检索） ----
    # 说明：GM 的 POST /api/v1/memories:search 硬编码了 LLM 适用性判定（实测 6.5-9s），
    # 无法关闭；GM 源码冻结不改。故读取侧直连 GM 的 PostgreSQL 自行做向量检索，
    # 相关性判断交给对话模型本身（注入 system_prompt）。详见 app/memory_retriever.py。
    memory_read_enabled: bool = True
    # GM 的 Postgres 连接串（可沿用 GM .env 的 MEMORY_DATABASE_URL，
    # postgresql+psycopg:// 方言会被自动规范化；localhost 会自动改 127.0.0.1 避免 IPv6 拖慢）
    gm_db_url: str = ""
    gm_tenant_id: str = "cmic-yun"
    # embedding 必须与 GM 写入侧同模型，否则向量不可比
    gm_embedding_api_key: str = ""
    gm_embedding_base_url: str = "https://api.siliconflow.cn/v1"
    gm_embedding_model: str = "BAAI/bge-m3"
    memory_read_limit: int = 5
    memory_read_scene_top_k: int = 3
    memory_read_timeout_ms: int = 2500
    # 余弦距离上限，超过视为不相关直接丢弃（实测相关命中 0.21，无关噪声 0.47+）
    memory_read_max_distance: float = 0.62
    # 是否复刻 GM 的证据链校验（关闭可再省 ~50ms，但可能召回已失效证据的记忆）
    memory_read_validate_evidence: bool = True
    # 短于该字符数不检索（"你好""继续"这类无检索价值）
    memory_read_min_query_chars: int = 2

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
