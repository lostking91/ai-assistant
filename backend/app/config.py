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

    # ---- 记忆写入侧的任务终态观测（原"记忆链路守护"，2026-10-15 精简为纯观测） ----
    # 历史背景：GM v2.4.0 worker 对同一会话有顺序保护——某任务终态 failed/partial/
    # cancelled 后，该会话后续抽取任务全部判 PRIOR_TASK_FAILED，写入永久停摆
    # （实测 2026-10-12 batch 7 卡链），故本地曾实现"轮换 GM 会话 + 重投/隔离"对策
    # （memory_link.py，已删除）。
    # 现状：GM v2.6.0 提交 a43b3ad 已从根上修复——worker.py 中 PRIOR_TASK_FAILED
    # 已完全移除，失败终态不再传播给后续任务（reports/add_context_validation.md：
    # "重试耗尽且租约过期的前序任务由 Add Worker 收尾，后续任务成功执行"）。
    # 因此只保留投递后的终态轮询观测（failed 仅记日志写回 outbox，不重投不轮换）。
    # 轮询间隔与总时间预算：实测 GM 单条抽取需 3-8s（含 LLM 调用），预算太短会
    # 拿不到终态；预算耗尽仍有在途任务时直接放弃本轮，残留发件留待下一次
    # flush/重启补投时再校验（幂等，无副作用）。
    memory_task_verify_interval_s: float = 2.0
    memory_task_verify_budget_s: float = 30.0

    # ---- GeneralMemory 记忆服务（读取侧） ----
    # 历史：GM v2.4.0 的 POST /api/v1/memories:search 硬编码 LLM 适用性判定（实测
    # 6.5-9.1s 无法关闭），故当时直连 GM PostgreSQL 自建向量检索（memory_retriever.py）。
    # 现状：GM v2.6.0 起 search 支持 use_model=false（提交 031ea68），关闭后纯向量
    # 检索，稳态 0.46-0.56s；与自建检索做过 7 组真实 query 等价性实测——返回 ID
    # 序列逐位完全一致（7/7 EXACT）。故默认切 GM API，自建直连保留为回退。
    # 后端选择：gm_api=调 GM HTTP API（推荐）；local=直连 GM Postgres 自建检索。
    memory_read_backend: str = "gm_api"
    memory_read_enabled: bool = True
    # GM 的 Postgres 连接串（仅 local 后端使用；可沿用 GM .env 的 MEMORY_DATABASE_URL，
    # postgresql+psycopg:// 方言会被自动规范化；localhost 会自动改 127.0.0.1 避免 IPv6 拖慢）
    gm_db_url: str = ""
    gm_tenant_id: str = "cmic-yun"
    # embedding 必须与 GM 写入侧同模型，否则向量不可比（仅 local 后端使用；
    # gm_api 后端由 GM 服务端自行向量化，无需本地配置）
    gm_embedding_api_key: str = ""
    gm_embedding_base_url: str = "https://api.siliconflow.cn/v1"
    gm_embedding_model: str = "BAAI/bge-m3"
    memory_read_limit: int = 5
    memory_read_scene_top_k: int = 3
    memory_read_timeout_ms: int = 2500
    # 余弦距离上限，超过视为不相关直接丢弃（实测相关命中 0.21，无关噪声 0.47+）。
    # 仅 local 后端生效：GM API 的 SearchResponse 只返回 id/memory/cognitive_type，
    # 不含 distance，调用侧无法做距离截断（相关性判断交给对话模型，prompt 已注入规则）。
    memory_read_max_distance: float = 0.62
    # 是否复刻 GM 的证据链校验（仅 local 后端生效；GM API 恒开 validate_user_evidence）
    memory_read_validate_evidence: bool = True
    # 短于该字符数不检索（"你好""继续"这类无检索价值）
    memory_read_min_query_chars: int = 2

    # ---- GeneralMemory 用户画像注入（2026-10-15 新增，与记忆检索并行） ----
    # 每轮对话在记忆检索的同时拉取 GET /api/v1/profiles（用 ai_assistant 接入凭证，
    # 管理凭证读 profiles 会 403），渲染进 system_prompt 的"# 用户画像"小节。
    # 画像是 GM 治理链路沉淀的长期用户属性（如"用户名叫老王""偏好简洁表格"），
    # 与 query 无关、每轮全量返回，故按条数上限截断注入。
    memory_profile_enabled: bool = True
    # include_inferred=true 时同时返回系统推断画像（assertion_kind=inferred，低置信度）；
    # GM 侧还受 MEMORY_PROFILE_ALLOW_INFERRED 环境变量门禁（已在 GM .env 开启）。
    memory_profile_include_inferred: bool = True
    # 注入 prompt 的画像条数上限（GM 端点无 limit 参数，客户端截断；当前实测 16 条）
    memory_profile_limit: int = 20
    # 画像拉取独立超时（毫秒）。实测稳态 0.3-0.9s；GM 重启后首次请求可能挂起 60s，
    # 超时即降级为"本轮无画像"，绝不阻塞对话。
    memory_profile_timeout_ms: int = 2500

    # ---- gm-memory MCP（GeneralMemory 只读工具的 MCP 封装，stdio 子进程） ----
    # 开启后模型可主动检索长期记忆/查任务状态/查画像（mcp__gm-memory__* 三工具）。
    # 与自动记忆注入（memory_read_*）互补：注入是"系统每轮自动附带"，
    # MCP 是"模型按需主动查询"。写入仍走 save_memory + outbox，不经 MCP。
    mcp_gm_memory_enabled: bool = True
    # 逗号分隔的 server 侧工具名白名单（空=全部启用）
    mcp_gm_memory_tools: str = ""
    # MCP 工具执行超时（秒），同时透传给 server 作 GM HTTP 超时
    mcp_gm_memory_timeout_s: float = 15.0

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
