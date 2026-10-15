# AI 助手 Demo（ai-assistant）

基于 **AgentScope 2.x + FastAPI + Vue 3** 的对话式 AI 助手。后端运行 ReAct 智能体（通义千问 `qwen-plus`），前端为暖色现代风格（方案3 · Anthropic Custom）的聊天界面，支持流式输出、工具调用、Markdown 渲染、会话持久化与历史会话管理。

> 本项目是「记忆服务集成」系列任务的载体：**任务1（AI 助手 Demo）已完成**；**任务2（GeneralMemory 接入）写入侧 + 读取侧均已完成**——写入走 `save_memory` 工具 + 回合结束钩子两条路径，读取走「每轮**并行**检索长期记忆 + 用户画像并动态注入 system_prompt」的无工具召回（v1.3.1 起双通道），且召回明细在前端「本轮记忆」面板对用户可见。详见[十二、GeneralMemory 记忆接入](#十二generalmemory-记忆接入)与文末[后续规划](#十四后续规划)。

### 版本：v1.3.1（2026-10-15）

相对 v1.3.0 的两项主要改造（记忆召回 → 记忆+画像双通道，且召回过程对用户可见）：

| # | 改造 | 内容 | 影响面 |
|---|---|---|---|
| 1 | **用户画像自动注入（后端）** | 每轮召回时与记忆检索**并行**拉取 GM 画像（`GET /api/v1/profiles?include_inferred=true`），渲染为「# 用户画像」小节注入 system_prompt；两路**独立降级**互不拖累；召回明细（记忆+画像）经 SSE `memory` 事件结构化透传 | `memory_retriever.py`、`agent.py`、`config.py`、`.env.example` |
| 2 | **「本轮记忆」可展开面板（前端）** | AI 气泡内新增 🧠 面板：召回中显示"正在回忆…"，完成后显示"本轮记忆：N 条记忆 · M 条画像"，点开可逐条查看记忆（事实/推断+相似度+耗时）与画像（明说/推断+业务域），降级时显示原因 | `MessageBubble.vue`、`ChatWindow.vue`、`App.vue`、`api/chat.js` |

> v1.3.0 的两项改造（gm-memory MCP server、工具权限门禁）见下表（保留）：

| # | 改造 | 内容 | 影响面 |
|---|---|---|---|
| 1 | **gm-memory MCP server（任务4 完成）** | 自研 stdio 形态 MCP server（FastMCP）封装 GM 三个只读端点，模型可按需主动查记忆/任务状态/画像；共享连接随 lifespan 启停，GM 侧零改动、不占新端口 | `mcp_server/gm_memory_server.py`(新)、`app/mcp_manager.py`(新)、`builtin_tools.py`、`agent.py`、`main.py`、`config.py` |
| 2 | **工具权限门禁（机制化根治"无声卡死"）** | 所有工具进 Toolkit 前用**生产同款权限引擎**预跑判定，ASK/DENY 拒绝注册（启动期抛错）；配套审计脚本与 `/api/permissions` 端点随时输出权限矩阵 | `app/tools/permission_gate.py`(新)、`builtin_tools.py`、`main.py`、`scripts/verify_permissions.py`(新) |

> v1.2.0 的三项改造（读侧切 GM API、移除 memory_link、集成 139邮箱）见下表（保留）：

| # | 改造 | 内容 | 影响面 |
|---|---|---|---|
| 1 | **记忆读侧切 GM 官方 API** | 默认后端由「直连 GM Postgres 自建向量检索」改为调 GM `POST /api/v1/memories:search`（`use_model=false` 纯向量检索）；自建检索降级为可回退的 `local` 后端 | `memory_retriever.py`、`config.py` |
| 2 | **移除记忆链路守护（memory_link）** | GM v2.6.0 提交 `a43b3ad` 已修复「前序任务失败导致会话级阻塞」，会话轮换/阻断检测失去存在意义 → 删除 313 行守护模块及其端点、建表 DDL、死配置；写侧精简为纯观测 | `memory_link.py`(删)、`agent.py`、`main.py`、`builtin_tools.py`、`database.py`、`config.py`、`memory_client.py` |
| 3 | **集成 139 邮箱 Skill** | vendored 官方 skill 包（`backend/skills/139-mail/`）+ 适配层工具 `mail139`（子进程调用，flag 黑名单 + 两步确认） | `tools/mail139.py`(新)、`skills/`、`builtin_tools.py`、`requirements.txt`、`.gitignore` |

> 附带：前端流式渲染性能优化（delta 合帧 + 渲染节流 + 滚动优化）与 Vue 3 响应式陷阱修复，见 [十三、常见问题排查](#十三常见问题排查)。

---

## 目录

- [一、技术栈与架构](#一技术栈与架构)
- [二、目录结构](#二目录结构)
- [三、功能清单](#三功能清单)
- [四、环境要求与已安装软件](#四环境要求与已安装软件)
- [五、快速开始](#五快速开始)
- [六、启动与关闭](#六启动与关闭)
- [七、配置说明（.env）](#七配置说明env)
- [八、API 接口文档](#八api-接口文档)
- [九、数据库设计](#九数据库设计)
- [十、内置工具](#十内置工具)
- [十一、会话管理逻辑](#十一会话管理逻辑)
- [十二、GeneralMemory 记忆接入](#十二generalmemory-记忆接入)
- [十三、常见问题排查](#十三常见问题排查)
- [十四、后续规划](#十四后续规划)
- [附：UI 设计稿](#附ui-设计稿)

---

## 一、技术栈与架构

### 整体架构

```
┌─────────────────────┐         HTTP / SSE          ┌──────────────────────────┐
│   前端 (Vue 3)       │  ────────────────────────>  │   后端 (FastAPI)          │
│   Vite :5173         │   /api 经 Vite 代理转发      │   Uvicorn :8000           │
│                      │  <────────────────────────  │                          │
│  Element Plus UI     │      流式事件 (SSE)          │  AgentScope 2.x ReAct    │
│  marked + hljs 渲染   │                             │   ├─ DashScope qwen-plus │
└─────────────────────┘                             │   ├─ Toolkit（8工具，全经  │
                                                     │   │   权限门禁注册）        │
                                                     │   │   └─ gm-memory MCP   │
                                                     │   │      (stdio 子进程)   │
                                                     │   └─ AgentState 持久化    │
                                                     │            │             │
                                                     │            ▼             │
                                                     │   SQLite (assistant.db)  │
                                                     │   sessions/memory_outbox │
                                                     └───────────┬──────────────┘
                                                                 │ HTTP（Bearer + Idempotency-Key）
                                                                 ▼
                                                     ┌──────────────────────────┐
                                                     │  GeneralMemory (GM) 服务   │
                                                     │  API :8001 + Worker×4角色  │
                                                     │  PostgreSQL :5433 (Docker)│
                                                     │  写:memories:add/读:search │
                                                     └──────────────────────────┘
```

- **前后端独立部署**，通过 REST + SSE 通信。
- 前端开发服务器用 **Vite 代理**把 `/api` 转发到 `http://127.0.0.1:8000`，规避跨域并让 SSE 正常工作。
- 后端把 AgentScope 的 **`AgentState` 整体序列化**存入 SQLite，保留完整上下文与工具调用轨迹。
- **记忆写入侧**：两条路径写 GM——① `save_memory` 工具（用户明确要求记住，同步等结果）；② 回合结束钩子（每轮 user+assistant 消息，经本地 `memory_outbox` 表落库后异步投递，重启可重发）。
- **记忆读取侧（v1.2.0 起走 GM 官方 API）**：每轮对话前调 GM `POST /api/v1/memories:search`（`use_model=false` 纯向量检索），把召回结果渲染进 `system_prompt` 动态注入；无 `search_memory` 工具（相关性判定交给对话模型本身，零额外延迟）。默认后端 `gm_api`，`local`（直连 GM Postgres 自建检索）保留为回退。详见[第十二章](#十二generalmemory-记忆接入)。
- **GM 会话映射（v1.2.0 起简化）**：本地 `session_id` 直接作为 GM 会话 ID，读写两侧均不再做会话轮换/解析（原 memory_link 守护已随 GM `a43b3ad` 修复移除）。

### 技术选型

| 层        | 技术                                | 说明                                   |
| -------- | --------------------------------- | ------------------------------------ |
| 智能体框架    | AgentScope 2.0.9                  | ReAct 智能体 + Toolkit + AgentState 持久化 |
| 大模型      | 通义千问 `qwen-plus`                  | 通过 DashScope 接入，支持流式                 |
| 后端框架     | FastAPI + Uvicorn                 | 异步、SSE 流式响应、自动 API 文档                |
| 配置管理     | pydantic-settings + python-dotenv | 从 `.env` 读取，避免硬编码密钥                  |
| 持久化      | SQLite（标准库 sqlite3）               | 异步端点经 `asyncio.to_thread` 调用         |
| 前端框架     | Vue 3（`<script setup>`）+ Vite 6   | 组合式 API                              |
| UI 组件库   | Element Plus                      | 图标、弹窗、输入等                            |
| Markdown | marked + highlight.js + DOMPurify | 代码高亮、表格渲染、XSS 过滤                     |
| 样式       | SCSS + CSS 变量                     | 方案3 暖色主题                             |

---

## 二、目录结构

```
ai-assistant/
├── README.md                     # 本文档
├── backend/                      # 后端（FastAPI + AgentScope）
│   ├── .env                      # 环境变量（API Key、模型、端口等）★需配置
│   ├── requirements.txt          # Python 依赖
│   ├── venv/                     # Python 虚拟环境（已安装依赖）
│   ├── data/
│   │   └── assistant.db          # SQLite 数据库（运行时自动创建）
│   └── app/
│       ├── __init__.py
│       ├── main.py               # FastAPI 入口，路由与 lifespan（flush outbox + MCP 启停）
│       ├── config.py             # 配置（读取 .env，含 GM 记忆读写与 MCP 配置项）
│       ├── models.py             # 请求/响应 Pydantic 模型
│       ├── agent.py              # AgentScope 智能体封装、会话管理、流式对话、回合记忆钩子、记忆召回注入
│       ├── database.py           # SQLite 持久化（sessions + memory_outbox）
│       ├── memory_client.py      # GM 写入侧客户端（契约适配、outbox 投递、结果归纳）
│       ├── memory_retriever.py   # GM 读取侧客户端（记忆+画像双通道并行召回、独立降级，渲染 system_prompt 片段）
│       ├── mcp_manager.py        # gm-memory MCP 共享连接管理（随 lifespan 启停，断线重连，v1.3.0 新增）
│       └── tools/
│           ├── __init__.py
│           ├── builtin_tools.py  # 内置工具实现（get_current_time/calculator/python_interpreter/save_memory + mail139 装配 + MCP 注册）
│           ├── permission_gate.py # 工具权限门禁（注册前用生产同款引擎强制校验，v1.3.0 新增）
│           └── mail139.py        # 139邮箱 Skill 适配层（子进程调用 vendored 官方脚本，flag 黑名单+两步确认）
│   ├── mcp_server/
│   │   └── gm_memory_server.py   # gm-memory MCP server（FastMCP stdio，GM 只读三工具，v1.3.0 新增）
│   ├── scripts/
│   │   ├── c_step_agent_id_test.py  # GM agent_id/source_system 过滤实测脚本（可重跑）
│   │   ├── smoke_gm_mcp.py          # gm-memory MCP stdio 冒烟测试（connect→list→审计→真实调用）
│   │   └── verify_permissions.py    # 权限审计脚本（离线输出全量权限矩阵，退出码即结论）
│   └── skills/
│       └── 139-mail/             # vendored 官方 139邮箱 Skill 包（运行依赖，凭据文件已 .gitignore）
├── frontend/                     # 前端（Vue 3 + Vite）
│   ├── package.json              # 依赖与脚本
│   ├── vite.config.js            # Vite 配置（@ 别名 + /api 代理）
│   ├── index.html                # HTML 入口
│   ├── node_modules/             # 已安装依赖
│   └── src/
│       ├── main.js               # Vue 入口（注册 Element Plus、导入样式）
│       ├── App.vue               # 根组件（布局 + 状态管理 + SSE 对接）
│       ├── api/
│       │   └── chat.js           # 后端 API 封装（含 SSE 流式解析）
│       ├── utils/
│       │   └── markdown.js       # Markdown 渲染 + 代码高亮
│       ├── styles/
│       │   └── variables.scss    # 主题 CSS 变量与全局样式
│       └── components/
│           ├── SessionSidebar.vue  # 左侧会话栏（分组、重命名、删除）
│           ├── ChatWindow.vue      # 消息列表区（自动滚动、欢迎页）
│           ├── MessageBubble.vue   # 消息气泡（Markdown、工具调用、流式光标、「本轮记忆」可展开面板）
│           └── InputArea.vue       # 输入框（自适应高度、Enter 发送）
└── design-demos/                 # UI 设计稿（参考用）
    ├── 1-gallery-dark.html       # 方案1
    ├── 2-claude-design.html      # 方案2
    └── 3-anthropic-custom.html   # 方案3 ★当前采用
```

---

## 三、功能清单

### 已实现（任务1 + 任务2 + v1.2.0）

| 功能               | 说明                                                       | 状态     |
| ---------------- | -------------------------------------------------------- | ------ |
| 流式对话             | SSE 逐字输出，前端实时渲染                                          | ✅      |
| ReAct 智能体        | 基于 AgentScope 2.x，自动决策是否调用工具                             | ✅      |
| 内置工具             | `get_current_time` / `calculator` / `python_interpreter` | ✅      |
| Markdown 渲染      | 标题、列表、表格、代码块高亮（highlight.js 暗色主题）                        | ✅      |
| 会话持久化            | SQLite 存储 AgentState，重启不丢上下文                             | ✅      |
| 历史会话栏            | 左侧按「今天/昨天/7天内/更早」分组展示                                    | ✅      |
| 会话切换             | 点击历史会话可切回继续对话（带 `resume`）                                | ✅      |
| 闲置超时             | 会话超 30 分钟无新增，再对话自动开新会话                                   | ✅      |
| 会话管理             | 新建、重命名、删除会话                                              | ✅      |
| 可折叠侧边栏           | 左侧栏可收起/展开                                                | ✅      |
| 输入体验             | 自适应高度、Enter 发送 / Shift+Enter 换行、IME 感知                   | ✅      |
| 停止生成             | 流式过程中可中断                                                 | ✅      |
| 未配 Key 提示        | 无 API Key 时界面显示警告条，接口返回明确错误                              | ✅      |
| 工具调用可视化          | 气泡内展示工具调用 chip                                           | ✅      |
| `save_memory` 工具 | 用户明确要求记住时，同步写入 GM 并按结果语义如实回复                             | ✅（任务2） |
| 回合记忆钩子           | 每轮 user+assistant 消息经 outbox 异步写入 GM                     | ✅（任务2） |
| outbox 可靠投递      | 先落 SQLite 再投递，幂等键重试，重启自动补发                               | ✅（任务2） |
| 记忆召回注入           | 每轮对话前检索长期记忆并动态注入 system_prompt（v1.2.0 默认走 GM API）        | ✅（v1.2.0） |
| `mail139` 工具     | 139邮箱 Skill：搜索/阅读/发送邮件、附件、日程、通讯录、网盘（vendored 官方包+适配层）  | ✅（v1.2.0） |
| `gm-memory` MCP server | 自研 stdio MCP server 封装 GM 只读三工具（memory_search / memory_task_status / memory_profile），模型可按需主动查记忆 | ✅（v1.3.0） |
| 工具权限门禁           | 所有工具注册前经生产同款权限引擎强制校验，ASK/DENY 拒绝注册；`/api/permissions` 端点 + `verify_permissions.py` 脚本随时出权限矩阵 | ✅（v1.3.0） |
| 用户画像自动注入         | 每轮召回与记忆检索**并行**拉取 GM 画像（含 inferred），渲染「# 用户画像」小节注入 system_prompt；两路独立降级互不拖累 | ✅（v1.3.1） |
| 「本轮记忆」可展开面板      | AI 气泡内 🧠 面板：召回中"正在回忆…"→ 完成后"N 条记忆 · M 条画像"，逐条可查（事实/推断、相似度、耗时、业务域），降级显示原因 | ✅（v1.3.1） |

### 待实现（后续任务）

| 功能                             | 所属任务      | 状态      |
| ------------------------------ | --------- | ------- |
| 记忆管理平台                         | 任务3       | ⏳ 主体已在 memory-platform 项目落地 |

> 原「任务4：gm-memory MCP server」已于 v1.3.0 完成——GM 官方虽暂缓 MCP，本项目以**自研 stdio MCP server 封装 GM HTTP API** 的形态落地（GM 侧零改动），而非等待官方实现。

> 原「任务2 延后项：检索/召回侧」已完成——采用**无工具召回**（每轮自动检索注入 system_prompt）而非 `search_memory` 工具，相关性判定交给对话模型本身，避免多一轮 LLM 往返。

---

## 四、环境要求与已安装软件

> 以下版本均为**当前磁盘实测**结果（非声明值）。

### 运行时环境

| 软件      | 版本                | 位置 / 说明                 |
| ------- | ----------------- | ----------------------- |
| Python  | 3.13.14           | 后端 venv：`backend/venv/` |
| Node.js | v22.22.2          | 受管运行时                   |
| npm     | 10.9.7            | 随 Node 安装               |
| 操作系统    | Windows（中文，管理员权限） | —                       |

### 后端 Python 包（`backend/venv`）

| 包                 | 实测版本    | 用途                       |
| ----------------- | ------- | ------------------------ |
| agentscope        | 2.0.9   | 智能体框架（核心）                |
| fastapi           | 0.142.4 | Web 框架                   |
| uvicorn           | 0.54.0  | ASGI 服务器                 |
| pydantic          | 2.13.5  | 数据模型                     |
| pydantic-settings | 2.15.0  | 配置管理                     |
| python-dotenv     | 1.2.4   | .env 解析                  |
| dashscope         | 1.27.7  | 通义千问 SDK                 |
| httpx             | 0.28.1  | GM 记忆服务 HTTP 客户端（读写两侧） |
| psycopg           | 3.3.6   | 读侧 `local` 后端直连 GM Postgres（回退用） |
| mcp               | 1.30.0  | Model Context Protocol SDK（gm-memory MCP server + AgentScope MCPClient，v1.3.0 起显式声明） |
| requests / cryptography / keyring | ≥2.31 / ≥42 / ≥25 | 139邮箱 Skill（vendored）依赖，v1.2.0 新增 |

### 前端 npm 包（`frontend/node_modules`）

| 包                       | 实测版本    | 用途                       |
| ----------------------- | ------- | ------------------------ |
| vue                     | 3.5.43  | 前端框架                     |
| vite                    | 6.4.4   | 构建/开发服务器                 |
| element-plus            | 2.14.7  | UI 组件库                   |
| @element-plus/icons-vue | 2.3.2   | 图标                       |
| marked                  | 15.0.12 | Markdown 解析              |
| highlight.js            | 11.12.0 | 代码高亮                     |
| dompurify               | 3.4.16  | XSS 过滤                   |
| sass                    | 1.105.1 | SCSS 编译                  |
| @vitejs/plugin-vue      | 5.2.4   | Vite Vue 插件              |
| esbuild                 | 0.25.12 | 依赖预构建（含 win32-x64 平台二进制） |

> ⚠️ **注意**：`@esbuild/win32-x64` 平台二进制曾缺失导致 Vite 启动失败，已手动补装（见[常见问题](#1-vite-启动报错-esbuildwin32-x64-could-not-be-found)）。

---

## 五、快速开始

### 前置：配置 DashScope API Key（必需）

要真正对话，必须填入真实的通义千问 API Key：

1. 获取地址：<https://dashscope.console.aliyun.com/apiKey>
2. 编辑 `backend/.env`，把占位符替换为真实密钥：
   ```ini
   DASHSCOPE_API_KEY=sk-你的真实密钥
   ```
3. 重启后端服务生效。

> 未配置 Key 时，界面会显示黄色警告条，对话接口返回 `503` 与明确提示，其余接口（健康检查、会话列表等）仍可正常使用。

### 首次安装依赖（已安装可跳过）

**后端**（清华源无 agentscope，必须指定官方源）：

```bash
cd backend
python -m venv venv
./venv/Scripts/python.exe -m pip install -r requirements.txt -i https://pypi.org/simple
```

**前端**：

```bash
cd frontend
npm install
# 若 esbuild 平台二进制缺失（Windows 偶发），补装：
npm install @esbuild/win32-x64@0.25.12 --no-save
```

---

## 六、启动与关闭

> 以下命令在 **Git Bash** 下执行（当前环境默认 shell）。Windows PowerShell/CMD 用户请把路径分隔符 `\` 替换、并使用 `venv\Scripts\python.exe`。

### 运行拓扑与端口

| 进程 | 端口 | 说明 | 日志文件 |
|---|---|---|---|
| 后端 uvicorn（`app.main:app`） | 127.0.0.1:8000 | 对话/会话/记忆写入 API | `backend/backend.log` |
| 前端 Vite dev | 127.0.0.1:5173 | 聊天界面，`/api` 代理到 :8000 | `frontend_vite.log`（项目根） |
| （依赖）GM API | 127.0.0.1:8001 | 启用记忆时需要，见[第十二章](#121-gm-环境启动三件套) | — |
| （依赖）GM Postgres | 127.0.0.1:5433 | docker 容器 `memory-cmic-postgres` | — |

### 实际后台启动（当前采用，可复现）

> 后台方式不加 `--reload`；改代码或改 `.env` 后需手动重启后端才生效。前端用受管 node 直接跑 `vite.js`，避免依赖 PATH 里的 `npm`。

```bash
# ① 后端（:8000），日志追加到 backend.log
cd /f/proj_test/ai2026/memory-research/ai-assistant/backend
./venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >> backend.log 2>&1 &

# ② 前端（:5173），受管 node 跑 vite.js，日志追加到项目根 frontend_vite.log
cd /f/proj_test/ai2026/memory-research/ai-assistant/frontend
"C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6/node.exe" node_modules/vite/bin/vite.js >> ../frontend_vite.log 2>&1 &
```

启动后验证：

```bash
sleep 5
curl -s http://127.0.0.1:8000/api/health                                       # {"status":"ok",...}
curl -s -o /dev/null -w "frontend HTTP %{http_code}\n" http://127.0.0.1:5173/   # 200
```

### 启动后端（:8000）— 前台/开发模式

```bash
cd backend
./venv/Scripts/python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

- 也可用：`./venv/Scripts/python.exe -m app.main`（`main.py` 内置了 uvicorn 启动）。
- 启动成功标志：日志出现 `Uvicorn running on http://0.0.0.0:8000`，且 `数据库就绪`。
- 验证：浏览器或 `curl http://127.0.0.1:8000/api/health` 返回 JSON。
- 交互式 API 文档：<http://127.0.0.1:8000/docs>

### 启动前端（:5173）

```bash
cd frontend
npm run dev
```

- 需确保 Node 在 PATH 中。受管 Node 路径：  
  `C:/Users/Administrator/.workbuddy/binaries/node/versions/22.22.2-6`
- 启动成功后访问：<http://127.0.0.1:5173>
- `/api` 请求由 Vite 自动代理到后端 :8000，无需额外配置跨域。

### 关闭服务

- **前台运行**：在对应终端按 `Ctrl + C`。
- **后台/找不到终端**：按端口查杀进程（PowerShell）：
  ```powershell
  # 查占用端口的 PID
  netstat -ano | findstr :8000
  netstat -ano | findstr :5173
  # 结束进程（替换 <PID>）
  taskkill /PID <PID> /F
  ```

### 启动顺序建议

1. （启用记忆时）先启动 **GM 三件套**：PostgreSQL 容器 → GM API(:8001) → GM Worker，见[第十二章](#121-gm-环境启动三件套)；
2. 启动**后端**（:8000），确认 `/api/health` 可访问，日志出现"记忆服务已启用"；
3. 启动**前端**（:5173），打开浏览器即可使用。

> GM 未启动也不影响对话：`save_memory` 会返回"记忆服务暂时不可用"，回合钩子投递失败进 outbox 重试/待补发，后端启动时会自动补投。

---

## 七、配置说明（.env）

配置文件位于 `backend/.env`，由 `app/config.py` 的 pydantic-settings 读取。

| 变量                        | 默认值                                           | 说明                   |
| ------------------------- | --------------------------------------------- | -------------------- |
| `DASHSCOPE_API_KEY`       | `YOUR_DASHSCOPE_KEY`                          | ★通义千问密钥，必须替换为真实值     |
| `MODEL_NAME`              | `qwen-plus`                                   | 使用的模型名               |
| `STREAM_ENABLED`          | `true`                                        | 是否启用流式输出             |
| `HOST`                    | `0.0.0.0`                                     | 后端监听地址               |
| `PORT`                    | `8000`                                        | 后端端口                 |
| `CORS_ORIGINS`            | `http://localhost:5173,http://127.0.0.1:5173` | 允许的跨域来源（逗号分隔）        |
| `SESSION_TIMEOUT_MINUTES` | `30`                                          | 会话闲置超时（分钟），超时再对话开新会话 |
| `DATABASE_PATH`           | `backend/data/assistant.db`                   | SQLite 路径（代码内默认，可覆盖） |
| `AGENT_NAME`              | `AI助手`                                        | 智能体名称                |
| `MAX_ITERS`               | `20`                                          | ReAct 最大迭代轮数         |

### 记忆服务配置（写入侧）

| 变量                   | 默认值                     | 说明                                                                         |
| -------------------- | ----------------------- | -------------------------------------------------------------------------- |
| `MEMORY_ENABLED`     | `true`                  | 记忆写入总开关。关闭后 `save_memory` 不注册、回合钩子不投递                                      |
| `GM_BASE_URL`        | `http://127.0.0.1:8001` | GeneralMemory API 地址（读写两侧共用）                                              |
| `GM_TOKEN`           | （空）                     | ★GM Bearer Token，**为空则记忆功能整体禁用**（`memory_enabled()` 要求 token/base_url 均非空） |
| `GM_SOURCE_SYSTEM`   | `ai_assistant`          | GM 侧的来源系统标识                                                                |
| `GM_USER_ID`         | `gm_user_lw`            | GM 租户内用户 ID（当前租户 `cmic-yun`）                                               |
| `GM_WAIT_TIMEOUT_MS` | `15000`                 | `save_memory` 同步等待抽取结果的超时（毫秒），HTTP 超时在此基础上 +10s                            |

> **写侧不传 `agent_id`**（GM 会回落到凭证的 `caller_agent_id`，即 `ai_assistant`）；**读侧不传 `agent_id`/`source_system`**（跨 Agent 共享视角，符合个人助手定位）。经 v1.2.0 实测确认，详见 [12.7](#127-agent_id--source_system-过滤语义实测固化)。

### 记忆服务配置（读取侧，v1.2.0 起生效）

| 变量                        | 默认值                     | 说明                                                         |
| ------------------------- | ----------------------- | ---------------------------------------------------------- |
| `MEMORY_READ_BACKEND`     | `gm_api`                | 读侧后端：`gm_api`=调 GM HTTP search（推荐）；`local`=直连 GM Postgres 自建向量检索（回退用） |
| `MEMORY_READ_ENABLED`     | `true`                  | 记忆召回总开关。关闭后不检索、不注入 system_prompt                            |
| `MEMORY_READ_LIMIT`       | `5`                     | 召回条数上限（GM 侧 limit 合法区间 1-50）                              |
| `MEMORY_READ_TIMEOUT_MS`  | `2500`                  | 单次检索超时；超时即降级为「本轮无记忆」，**绝不阻塞对话链路**                         |
| `MEMORY_READ_MIN_QUERY_CHARS` | `2`                 | 用户输入短于该长度时跳过检索（无检索价值）                                     |
| `GM_DB_URL`               | （空）                     | 仅 `local` 后端需要：GM PostgreSQL 连接串（只读）                        |
| `GM_TENANT_ID`            | `cmic-yun`              | 仅 `local` 后端需要：租户 ID                                        |
| `GM_EMBEDDING_*`          | SiliconFlow / `BAAI/bge-m3` | 仅 `local` 后端需要：本地向量化配置（`gm_api` 后端由 GM 服务端自行向量化）      |
| `MEMORY_READ_MAX_DISTANCE` | `0.62`                 | 仅 `local` 后端生效：余弦距离截断阈值（`gm_api` 响应不含 distance，故不可用）        |
| `MEMORY_READ_VALIDATE_EVIDENCE` | `true`            | 仅 `local` 后端生效：证据链完整性校验                                    |
| `MEMORY_TASK_VERIFY_INTERVAL_S` / `_BUDGET_S` | `2.0` / `30.0` | 写侧任务终态观测（原「记忆链路守护」，v1.2.0 精简为**纯观测**：只记日志，不再轮换会话、不再阻断） |

> **已删除的死配置**（v1.2.0）：`MEMORY_LINK_GUARD_ENABLED`、`MEMORY_LINK_MAX_GENERATION`、`MEMORY_LINK_MAX_ROTATE_PER_ITEM` —— 对应的会话轮换守护逻辑随 GM `a43b3ad` 修复一并移除。

### 用户画像配置（v1.3.1 新增）

| 变量                        | 默认值      | 说明                                                         |
| ------------------------- | -------- | ---------------------------------------------------------- |
| `MEMORY_PROFILE_ENABLED`  | `true`   | 画像注入总开关（还需 `GM_BASE_URL`+`GM_TOKEN` 齐全才生效）。关闭后仅记忆召回，画像路降级 `profile_disabled_or_unconfigured` |
| `MEMORY_PROFILE_INCLUDE_INFERRED` | `true` | 是否包含治理推断出的画像（inferred）。需 GM 侧 `MEMORY_PROFILE_ALLOW_INFERRED=true` 才有 inferred 数据 |
| `MEMORY_PROFILE_LIMIT`    | `20`     | 注入条数上限。GM profiles 端点无 limit 参数，客户端截断：explicit 优先排序后取前 N |
| `MEMORY_PROFILE_TIMEOUT_MS` | `2500` | 画像拉取超时；超时独立降级为「本轮无画像」，**不影响记忆召回**                          |

> 画像拉取复用 `.env` 的 `GM_TOKEN`（ai_assistant 接入凭证）。注意**凭证边界**：该凭证走 `read_user_memory` 权限可查 profiles；GM 管理凭证查 `/profiles` 会 403。

### gm-memory MCP 配置（v1.3.0 新增）

| 变量                        | 默认值      | 说明                                                         |
| ------------------------- | -------- | ---------------------------------------------------------- |
| `MCP_GM_MEMORY_ENABLED`   | `true`   | MCP 记忆工具总开关（还需 `MEMORY_ENABLED=true` 且 server 脚本存在才生效）      |
| `MCP_GM_MEMORY_TOOLS`     | （空=全部）  | 工具白名单（逗号分隔 server 侧原始名）：`memory_search,memory_task_status,memory_profile` |
| `MCP_GM_MEMORY_TIMEOUT_S` | `15`     | MCP 工具执行超时（秒），同时透传给 server 作 GM HTTP 超时                    |

> MCP server 复用 `.env` 的 `GM_BASE_URL`/`GM_TOKEN`/`GM_USER_ID`（经 stdio 子进程 env 注入，token 不落命令行）。子进程用后端自身 venv python（`sys.executable`）拉起；`mcp` SDK（1.30.0）随 agentscope 依赖已在 venv 中，无需额外安装。


> `has_api_key` 判定逻辑：密钥非空且不以 `YOUR_` 开头才视为已配置。

---

## 八、API 接口文档

所有接口以 `/api` 为前缀。后端启动后可访问 <http://127.0.0.1:8000/docs> 查看自动生成的 Swagger 文档。

### 接口一览

| 方法     | 路径                            | 说明                        |
| ------ | ----------------------------- | ------------------------- |
| GET    | `/api/health`                 | 健康检查（含模型、API Key 状态、工具列表） |
| GET    | `/api/permissions`            | 权限审计：输出全量工具权限矩阵（可带 `?session_id=` 纳入 save_memory），v1.3.0 新增 |
| POST   | `/api/chat`                   | 非流式对话                     |
| POST   | `/api/chat/stream`            | 流式对话（SSE）                 |
| GET    | `/api/sessions?limit=50`      | 会话列表（按最近更新排序）             |
| GET    | `/api/sessions/{sid}/history` | 某会话的历史消息                  |
| PATCH  | `/api/sessions/{sid}`         | 重命名会话                     |
| DELETE | `/api/sessions/{sid}`         | 删除会话                      |

### 请求体：ChatRequest

```json
{
  "message": "用户输入的消息",       // 必填，min_length=1
  "session_id": "会话ID或null",     // 为空则新建会话
  "resume": false                   // true=强制续用指定会话（用户主动切回历史会话），忽略闲置超时
}
```

### 响应体

**ChatResponse（非流式）**

```json
{
  "session_id": "...",
  "reply": "智能体回复文本",
  "title": "会话标题",
  "is_new_session": false,
  "tool_calls": [{"name": "calculator", "input": {...}}]
}
```

**HealthResponse**

```json
{
  "status": "ok",
  "model": "qwen-plus",
  "api_key_configured": true,
  "tools": ["get_current_time", "calculator", "python_interpreter", "mail139"]
}
```

> 健康检查不绑定会话，因此 `tools` 里**不含 `save_memory`**（该工具需 session_id 上下文才注册，属预期行为）；`mail139` 在 skill 目录就位时出现（`skills/139-mail/scripts/common.py` 存在）。

**SessionItem（列表项）**：`{session_id, title, created_at, updated_at}`

**SessionHistory**：`{session_id, title, messages: [MessageItem]}`

**MessageItem**：`{role: "user"|"assistant", content, tool_calls: [], created_at}`

### SSE 流式事件（`/api/chat/stream`）

响应为 `text/event-stream`，每条报文形如 `data: {JSON}\n\n`，流结束时追加 `event: end`。事件类型：

| type          | 载荷字段                                                           | 说明          |
| ------------- | -------------------------------------------------------------- | ----------- |
| `meta`        | `session_id, is_new_session, title`                            | 本轮元信息（最先发出） |
| `memory_start` | （无载荷）                                                          | 开始记忆/画像召回（记忆或画像任一开关开启即发出；前端显示"正在回忆…"），v1.3.1 扩展 |
| `memory`      | `memory_hits, memory_elapsed_ms, memory_degraded, memory_reason, memory_items[{content, cognitive_type, similarity}], profile_hits, profile_total, profile_elapsed_ms, profile_degraded, profile_reason, profile_items[{key, value, assertion_kind, business_domains}]` | 本轮召回明细（记忆+画像双通道，结构化透传给前端「本轮记忆」面板），v1.3.1 扩展 |
| `tool_start`  | `name`                                                         | 开始调用某工具     |
| `tool_result` | `name, state`                                                  | 工具调用结束及状态   |
| `delta`       | `text`                                                         | 文本增量（逐字）    |
| `done`        | `reply, tool_calls, session_id, title, finished_reason, error, memory` | 本轮完成（`memory` 为召回明细字典，与 `memory` 事件同源） |
| `error`       | `message`                                                      | 出错（如未配 Key） |

**SSE 测试示例**：

```bash
curl -N -X POST http://127.0.0.1:8000/api/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"message":"现在几点？"}'
```

---

## 九、数据库设计

使用 SQLite，库文件 `backend/data/assistant.db`（首次启动自动创建）。

**表 `sessions`**

| 字段           | 类型      | 说明                                |
| ------------ | ------- | --------------------------------- |
| `session_id` | TEXT PK | 会话唯一 ID                           |
| `title`      | TEXT    | 会话标题（默认取首条用户消息前 20 字）             |
| `created_at` | TEXT    | 创建时间（ISO）                         |
| `updated_at` | TEXT    | 最近更新时间（ISO），用于排序与超时判定             |
| `state_json` | TEXT    | **AgentState 完整序列化**（含上下文与工具调用轨迹） |

- 索引：`idx_sessions_updated_at`（按 `updated_at DESC`）。
- 异步策略：所有 DB 操作经 `asyncio.to_thread` 包装，避免阻塞事件循环；每次操作新建连接，规避多线程共享问题。
- **持久化设计**：直接存 AgentScope 的 `AgentState`（而非自行重建消息历史），更可靠且保留工具调用轨迹。

**表 `memory_outbox`（任务2 新增）**

GM 写入侧发件箱：回合结束后先落库再异步投递，进程重启后可用原幂等键重发（GM 侧按 Idempotency-Key 去重）。

| 字段                          | 类型                       | 说明                                        |
| --------------------------- | ------------------------ | ----------------------------------------- |
| `id`                        | INTEGER PK AUTOINCREMENT | 自增主键                                      |
| `session_id`                | TEXT                     | 所属会话（v1.2.0 起同时就是 GM 会话 ID）              |
| `idempotency_key`           | TEXT UNIQUE              | GM 幂等键（UUID hex，1-128 ASCII）              |
| `payload_json`              | TEXT                     | AddRequest 完整载荷（含 messages）               |
| `status`                    | TEXT                     | `pending` / `sending` / `sent` / `failed`（存量行可能残留历史状态 `quarantined`） |
| `attempts`                  | INTEGER                  | 已尝试次数                                     |
| `task_id`                   | TEXT                     | 投递成功后 GM 返回的任务 ID                         |
| `gm_task_status`            | TEXT                     | GM 侧任务终态观测值（`_verify_tasks` 轮询回写：null/pending/processing/succeeded/partial/failed/cancelled） |
| `last_error`                | TEXT                     | 最近一次失败原因                                  |
| `gm_session_id` / `rotate_count` | TEXT / INTEGER       | **历史遗留列**（会话轮换机制时期产物，v1.2.0 起不再使用，迁移保留） |
| `created_at` / `updated_at` | TEXT                     | 时间戳                                       |

- 索引：`idx_outbox_status(status, id)`（`flush_outbox` 按状态扫描待发件）、`idx_outbox_gm_session(gm_session_id, status)`（历史遗留）。
- **表 `memory_link` 已移除（v1.2.0）**：原用于「本地会话→GM 会话映射」支撑阻断轮换守护，GM `a43b3ad` 修复后新库不再建此表；存量库中的表与数据保留不动（见 [12.8](#128-记忆链路守护memory_link移除说明v120)）。

---

## 十、内置工具

由 `backend/app/tools/builtin_tools.py` 实现（AgentScope 2.x 无内置时间/计算/解释器工具，故自研）。工具函数返回 `ToolChunk`，Google 风格 docstring 自动生成 JSON schema。

| 工具                   | 参数                | 说明                                            | 安全设计                                            |
| -------------------- | ----------------- | --------------------------------------------- | ----------------------------------------------- |
| `get_current_time`   | 无                 | 获取当前日期时间（东八区，含星期）                             | 只读                                              |
| `calculator`         | `expression: str` | 计算数学表达式，支持四则、幂、取余及 `sqrt/log/sin/cos/round` 等 | **AST 白名单**求值，杜绝 `eval` 注入                      |
| `python_interpreter` | `code: str`       | 受限沙箱执行 Python 代码，返回 stdout/stderr/异常          | **禁用高危内建**（`open/exec/eval/__import__/input` 等）；显式 ALLOW |
| `mail139`            | `action, args`    | 139邮箱：搜索/阅读/发送/草稿/附件/日程/通讯录/网盘/待办（vendored 官方 Skill 子进程调用，v1.2.0 新增） | skill 自带 confirm_token 两步确认 + 适配层 flag 黑名单（禁 `--scheduled`/`--base-url`）+ 路径锁定工作目录；显式 ALLOW |
| `save_memory`        | `content: str`    | 把用户明确要求记住的信息同步保存到 GM 长期记忆                     | 仅在**有会话上下文且记忆功能启用**时注册；标 `is_read_only` 走权限快速通道 |
| `mcp__gm-memory__memory_search` | `query: str, limit: int=5` | 语义检索长期记忆（GM `memories:search`，`use_model=false`，v1.3.0 新增） | MCP 只读工具（`readOnlyHint=True`）→ 引擎自动 ALLOW |
| `mcp__gm-memory__memory_task_status` | `task_id: str` | 查询 GM 记忆写入任务的执行状态（v1.3.0 新增） | 同上，只读 |
| `mcp__gm-memory__memory_profile` | `include_inferred: bool=False` | 查询 GM 沉淀的用户画像（v1.3.0 新增） | 同上，只读 |

**calculator 示例**：`"2 + 3 * 4"` → `14`；`"sqrt(144)"` → `12`

**python_interpreter 示例**：`print(sum(range(10)))` → `标准输出：45`

**mail139 说明**：`action` 为 enum 硬约束，共 **23 个取值**（由 `_ACTIONS` 表生成，模型无法造词）：`account / scope / auth / unbind / search / read / attachments / download / contacts / send / draft-list / draft-read / draft-send / mark / folders / move / label-add / calendar-view / calendar-search / calendar-detail / calendar-messages / calendar-add / disk-link`；`args` 为空格分隔的 `--flag value` 串（如 `--keyword 报销 --page-size 10`）。description 直接使用完整 docstring，避免显式短描述覆盖 schema 导致模型凭直觉造词。首次调用触发 139 邮箱 OAuth 授权（浏览器扫码），凭据落 `~/139mail/`（仓库外，且 `.gitignore` 兜底）。写操作（发信等）由模型层展示草稿等用户确认 + skill 层 confirm_token 双保险。

**save_memory 说明**：系统提示词约束——仅当用户明确表达"记住/记一下/以后记得"等意图时调用；content 须是完整、独立可理解的事实陈述；一次只保存一个主题。工具返回值按 GM 任务结果语义区分"保存成功/重复未新增/处理中/失败"，并明确指示模型**不得虚报保存成功**。详见[第十二章](#十二generalmemory-记忆接入)。

> 记忆检索**不设工具**：召回在每轮对话前自动执行并注入 system_prompt（见 12.6），相关性判定交给对话模型本身，省一轮 LLM 往返。

**gm-memory MCP 工具说明（v1.3.0）**：与自动召回互补——注入是"系统每轮自动附带"，MCP 工具是"模型按需主动查询"（如用户问"你还记得我说过……吗"或需核实注入未覆盖的历史事实）。server 为 `backend/mcp_server/gm_memory_server.py`（FastMCP stdio 子进程），由 `app/mcp_manager.py` 在后端启动时拉起共享连接、退出时回收；连接失败仅降级（MCP 工具缺席，不影响其它工具与会话）。**有意只暴露只读工具**：写入仍走 `save_memory` + outbox（MCPTool 每次调用临时构造拿不到 session_id 闭包，且写侧需要本地 SQLite 事务语义）。工具层永不抛异常，GM 错误被转成 `{"ok": false, "error": ...}` 可读结论。

### 工具权限门禁（v1.3.0，"无声卡死"的机制化解法）

**背景**：AgentScope 2.0.9 DEFAULT 权限模式下，未显式声明权限的工具被判 ASK → 工具 park 等待人工确认 → 前端无确认 UI → SSE 只发 `tool_start` 不发 `tool_result`，表现为无声卡死（v1.1.1 踩坑）。此前靠"记得给每个工具加 ALLOW"的人肉约定防坑，v1.3.0 起改为**机器强制**：

1. **注册门禁**（`app/tools/permission_gate.py`）：`build_toolkit` 中所有工具（含 MCP）一律经 `add_tool_gated()` 注册——用**生产同款引擎**（`PermissionEngine(AgentState().permission_context)`，与 `Agent.__init__` 的构造完全同源，是复刻不是近似）在注册前预跑 `check_permission`：
   - ALLOW → 放行注册 + INFO 日志；
   - ASK/DENY → **拒绝注册** + ERROR 日志；`strict=True` 时直接抛 `PermissionGateError`（启动期暴露，绝不带病上线）。会卡死的工具根本进不了 toolkit，模型看不到。
2. **审计脚本**：`./venv/Scripts/python.exe scripts/verify_permissions.py` 离线输出全量权限矩阵（含 save_memory/mail139/MCP 三工具），退出码 0=全 ALLOW、1=存在风险项；发布前跑一遍即可。
3. **审计端点**：`GET /api/permissions?session_id=xxx` 对运行中服务实时输出同一矩阵（`all_allow` 字段即结论），只评估不执行、无副作用。
4. **MCP 天然安全路径**：gm-memory 三工具全部标注 `readOnlyHint=True` → `MCPTool.is_read_only=True` → 引擎只读快速通道自动 ALLOW；门禁仍会复核。

> 新增工具的强制流程：FunctionTool 必须标 `is_read_only=True`（只读语义）或传 `permission=PermissionDecision(behavior=ALLOW, ...)`（有副作用但可信，写明理由），否则启动即失败。当前矩阵：8 工具全 ALLOW（见 `verify_permissions.py` 实测输出）。

---

## 十一、会话管理逻辑

核心在 `backend/app/agent.py` 的 `resolve_session(session_id, resume)`：

| 场景                   | 行为                        |
| -------------------- | ------------------------- |
| 未传 `session_id`      | 新建会话                      |
| 传了但库中不存在             | 新建会话（沿用传入 ID 便于前端对齐）      |
| 传了且存在，闲置 **超 30 分钟** | 新建会话                      |
| 传了且存在，`resume=True`  | **强制续用**（用户主动切回历史会话，忽略超时） |
| 传了且存在，未超时            | 续用                        |

- 超时阈值由 `.env` 的 `SESSION_TIMEOUT_MINUTES` 控制（默认 30）。
- 前端左侧栏点击历史会话切回时会带 `resume=true`，因此**即使超过 30 分钟也能继续原会话**；只有在当前会话内自然对话且闲置超时，才会开新会话。
- 会话状态损坏时自动降级为新会话，保证健壮性。
- **与 GM 的会话关系（v1.2.0 简化）**：本地 `session_id` **直接**作为 GM 会话 ID 使用（写侧 `enqueue_turn` 与读侧召回均如此）。v1.1.1 时期的「本地会话→GM 会话映射 + 轮换」已随 GM `a43b3ad` 修复移除，见 [12.8](#128-记忆链路守护memory_link移除说明v120)。

---

## 十二、GeneralMemory 记忆接入

> 决策范围：**写入侧**（`save_memory` 工具 + 回合钩子）+ **读取侧**（每轮自动召回注入 system_prompt）。GM 跑 **8001** 端口，AI 助手保持 **8000**；租户 `cmic-yun`，`user_id=gm_user_lw`；回合钩子仅提交 **user + assistant 两条消息**（不含工具调用中间消息）。

### 12.1 GM 环境启动（三件套）

GM 源码当前运行为 **v2.6.0**（`../gm-v2.6.0-analysis/`，生产数据卷已原地升级、不可回退；`../GeneralMemory/` v2.4.0 保留为只读参照，**不要启动它的服务**，会与 v2.6.0 抢 :8001/:5433）。最小三件套（Postgres + API + Worker）：

**① PostgreSQL 容器**（数据持久化，端口 5433 避开本机默认 5432）：

```bash
docker start memory-cmic-postgres
```

**② GM API**（:8001，Git Bash；GM 无 python-dotenv，必须先 source .env 注入环境变量）：

```bash
cd ../gm-v2.6.0-analysis
set -a && . ./.env && set +a
./.venv/Scripts/python.exe -m uvicorn memory_cmic.api:create_app \
  --factory --host 127.0.0.1 --port 8001
```

**③ GM Worker**（v2.6.0 支持 4 个角色：`add`/`maintenance`/`consolidation`/`profile`，当前生产 4 个全开；最小启用记忆只需 `add`）：

```bash
cd ../gm-v2.6.0-analysis
set -a && . ./.env && set +a
./.venv/Scripts/memory-cmic-worker.exe --role add
```

验证：`curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/docs` 返回 200。

> ⚠️ 本项目后端调 GM 的所有 httpx AsyncClient 必须 `trust_env=False`——开发环境注入的代理会把绝对 URI 转发坏导致 404（读侧 `memory_retriever.py` 与写侧 `memory_client.py` 均已内置）。

### 12.2 后端配置

`backend/.env` 增加记忆配置块（完整清单见[第七章](#七配置说明env)）：

```ini
# ---- 读写共用 ----
MEMORY_ENABLED=true                 # 写入总开关
GM_BASE_URL=http://127.0.0.1:8001   # GM API（读写两侧共用）
GM_TOKEN=<GM 签发的 Bearer Token>    # 为空则记忆功能整体静默禁用
GM_SOURCE_SYSTEM=ai_assistant
GM_USER_ID=gm_user_lw
GM_WAIT_TIMEOUT_MS=15000

# ---- 读取侧（v1.2.0）----
MEMORY_READ_ENABLED=true
MEMORY_READ_BACKEND=gm_api          # gm_api（推荐）| local（直连 PG 自建检索，回退用）
MEMORY_READ_LIMIT=5
MEMORY_READ_TIMEOUT_MS=2500

# ---- 用户画像注入（v1.3.1）----
MEMORY_PROFILE_ENABLED=true
MEMORY_PROFILE_INCLUDE_INFERRED=true
MEMORY_PROFILE_LIMIT=20
MEMORY_PROFILE_TIMEOUT_MS=2500
```

`GM_TOKEN` 为空时记忆功能整体静默禁用（`save_memory` 不注册、钩子不投递、召回不执行），助手其余功能不受影响。

### 12.3 两条写入路径

| 路径                   | 触发                | 消息内容                       | 投递方式                                                  | 结果语义                                             |
| -------------------- | ----------------- | -------------------------- | ----------------------------------------------------- | ------------------------------------------------ |
| **① save_memory 工具** | 用户明确说"记住…"，模型主动调用 | 单条 user 消息（套范围声明模板，见 12.5） | `wait_for_result=true` **同步**等 GM 抽取终态（15s 超时）        | 按任务状态如实回复（见 12.4）                                |
| **② 回合结束钩子**         | 每轮对话结束（流式/非流式都挂钩） | user + assistant 两条消息      | 先落 `memory_outbox` 表，再 **fire-and-forget** 异步投递，不阻塞响应 | 投递失败自动重试（0.5s/1s/2s 退避，最多 3 次），重启后 lifespan 自动补发 |

实现位置：`app/memory_client.py`（客户端+outbox，`enqueue_turn(session_id, user_text, assistant_text)`）、`app/tools/builtin_tools.py`（工具）、`app/agent.py`（钩子挂载点）。

**任务终态观测（v1.2.0 精简为纯观测）**：投递后会轮询 GM `tasks/{task_id}` 拿终态，仅**记日志 + 回写 `memory_outbox.gm_task_status`** 供排查，`failed` 不再触发任何补救动作（原「阻断检测 + 会话轮换」已移除，见 [12.8](#128-记忆链路守护memory_link移除说明v120)）。轮询参数：`MEMORY_TASK_VERIFY_INTERVAL_S=2.0`、`_BUDGET_S=30.0`。

### 12.4 GM 契约语义（实测固化）

- **幂等**：`Idempotency-Key` 必填（1-128 ASCII），随 outbox 持久化，重发用原键，GM 侧去重。
- **时间**：`occurred_at` 必须带时区，统一东八区 RFC3339；assistant 消息时间 +1s 保证单调。
- **载荷**：`AddRequest extra="forbid"` 且拒绝显式 null——可选字段一律省略不传。
- **大小**：单条 content ≤ 64KiB，超长按 UTF-8 安全截断。
- **结果判定（关键）**：

| GM 任务状态                            | results | 判定             | 对用户的说法         |
| ---------------------------------- | ------- | -------------- | -------------- |
| `succeeded`                        | 非空      | ✅ 保存成功         | "已记住…"         |
| `succeeded`                        | 空       | ❌ 未形成新记忆（重复事实） | "已收到，此前已有相同记忆" |
| `pending` / `processing`           | —       | ❌ 不算成功         | "已提交保存，稍后生效"   |
| `failed` / `partial` / `cancelled` | —       | ❌ 失败           | 如实告知未成功        |

> **注意**：`succeeded` + 空 results 还有一种成因——事实被治理规则**跳过**（如 SCOPE_UNRESOLVED），任务照样 succeeded。判断"是否真的存进去了"必须看 results 非空，必要时查 `memory_task.payload->'execution'->'diagnostics'`。

### 12.5 踩坑记录：SCOPE_UNRESOLVED 跳过（根因与修复）

**现象**：用户说"记住我的工号 GZ-2026-0888 和工位"，GM 任务 `succeeded` 但 `memory_item` 无新条目。

**根因**（GM `providers.py` `candidate_skip_reason()`）：GM 抽取治理要求每条事实标注业务适用范围；`scope_known=false` 的事实以 `SCOPE_UNRESOLVED` 被跳过（不进 results，任务仍 succeeded）。GM 抽取提示词只豁免"个人身份/生日/居住地"类事实，**工号/工位等工作身份类不在豁免清单**，裸的"请记住：X"模板下抽取模型保守判 `scope_known=false`。

**修复**：`save_memory` 的消息模板追加**中性范围声明**（`builtin_tools.py` `_SAVE_MEMORY_TEMPLATE`）：

```
请记住：{text}（范围说明：这是用户本人的信息。若内容未注明特定业务场景或项目限定，
视为通用个人信息；若内容本身写明场景或项目限定条件，按该限定标注，不要泛化。）
```

对照实验验证：身份类事实（工号/工位）正常入库；项目限定事实保留限定条件、不被泛化；入库记忆文本干净（范围声明只影响抽取，不污染 memory 内容）。

**关联行为——DISPUTE 冲突治理**：对同一属性反复提交不同值（如工号 4444→5555→6666），GM 会将相关 `memory_item` 置为 `disputed`（同一 `conflict_group_id`），后续任务报 `dispute recovery requires explicit user clarification` 而 failed。这是 GM 的**正确治理行为**（防事实漂移），不是 bug；恢复需用户对冲突值做显式澄清。测试时切忌用同属性不同值反复打靶。

### 12.6 读取侧召回（v1.2.0 起默认走 GM API）

**链路**：每轮对话前 `_recall_memory_block(message)` → `memory_retriever.recall()` → 渲染为 system_prompt 片段 → `build_system_prompt(memory_block)` 动态拼装（AgentScope 的 `Agent._get_system_prompt()` 每次模型调用都会重新拼）。

**双后端**（`MEMORY_READ_BACKEND` 切换）：

| 后端 | 实现 | 何时用 | 差异 |
|---|---|---|---|
| `gm_api`（默认） | GM `POST /api/v1/memories:search`，`use_model=false`（纯向量检索，不调 Qwen 适用性判定） | 常规 | 响应只含 `{id, memory, cognitive_type}`，**无 distance/similarity**；prompt 表头用「序号」列 |
| `local`（回退） | 直连 GM Postgres 复刻 `retrieval.py::search_task_memories`：时间有效性 + project_domains 过滤、双通道（scene/semantic）向量候选、余弦距离截断、证据链校验 | GM API 不可用或需距离信息时 | 需配 `GM_DB_URL`/`GM_EMBEDDING_*`；prompt 表头含「相关度」列 |

**切换依据（等价性实测，2026-10-15）**：7 组真实业务 query、limit=5，两后端返回的 memory ID 序列**逐位 EXACT 一致（7/7）**；稳态耗时持平（0.46-0.66s）。故默认切 `gm_api` 无语义损失，且不再依赖本地 embedding 密钥与 PG 连接。

**关键设计**：
- **不设 `search_memory` 工具**——召回自动执行、结果作为数据注入 prompt，相关性判定交给对话模型本身（模型本来就要推理，零额外延迟；设工具则每次召回多一轮 LLM 往返）。
- **绝不抛异常给对话链路**：超时（`MEMORY_READ_TIMEOUT_MS=2500`）、GM 非 2xx、任何异常都降级为「本轮无记忆」，只记 debug 日志。
- **短输入跳过**：用户输入 < `MEMORY_READ_MIN_QUERY_CHARS=2` 不检索（但**画像仍注入**——画像与 query 无关，见 12.11）。
- **v1.3.1 起为双通道**：`recall()` 内 `asyncio.gather` 并行执行记忆检索与画像拉取，详见 [12.11](#1211-用户画像注入与本轮记忆面板v131)。

### 12.7 agent_id / source_system 过滤语义（实测固化）

`SearchRequest` 可选过滤参数 `agent_id`、`source_system`（可组合，AND 关系）。实测 7 组组合（`backend/scripts/c_step_agent_id_test.py` 可重跑）：

| 过滤组合 | 行为 |
|---|---|
| 不传任何过滤 | **跨 Agent 共享视角**：同一 tenant+user 下所有 Agent 写入的记忆都可见 |
| `agent_id=X` | 只返回由 X 的任务抽取的记忆（精确隔离）；X 不存在则**返回空列表，不报错** |
| `source_system=X` | 直接过滤 `SourceRecord.source_system`（与凭证的 `allowed_source_systems` 鉴权是两个独立维度） |

**本项目结论：读写两侧都不传 `agent_id`。**
- 写侧：`AddRequest.agent_id` 可选，不传时 GM 回落到**凭证的 `caller_agent_id`**（ai-assistant 的 token 对应 `ai_assistant`）——入库归属正确。
- 读侧：个人助手定位下共享视角是**期望行为**（用户不管记忆是哪个 Agent 写的，都该被想起）。
- 注意：任一过滤参数存在时 GM 强制 `validate_user_evidence=True`，无匹配 source 直接返回空（`retrieval.py` `_matching_source_ids` + 282-283 行）。

**GM 记忆"软删除"标准姿势**（清理测试数据用）：GM 无撤销 add 的官方 API，须改两处生命周期状态——`memory_item.status='invalidated'`（检索只取 active）+ `memory_embedding.status='stale'`（该表 CHECK 只允许 active/stale；stale 恰好移出 `WHERE status='active'` 的 HNSW 部分索引）。两步都做才算移出召回链路。psql 用户是 `memory_cmic`（不是 postgres）。

### 12.8 记忆链路守护（memory_link）移除说明（v1.2.0）

**背景**：v1.1.1 曾引入 313 行的 `memory_link.py`，用于兼容 GM v2.4.0 的缺陷——同一会话内**前序 add 任务 failed 会导致后续任务被 `PRIOR_TASK_FAILED` 连坐拒绝**（会话级阻塞）。当时的对策是检测阻断 + 轮换 GM 会话 ID 就地重试。

**移除依据**：GM v2.6.0 提交 `a43b3ad` 已修复该缺陷（worker 不再传播 `PRIOR_TASK_FAILED`，失败任务不阻断同会话后续任务；佐证见 GM `reports/add_context_validation.md`）。守护失去存在意义。

**v1.2.0 移除清单**：
- 删除 `app/memory_link.py`（阻断检测 + 会话轮换 + 受害判定）；
- `agent.py`：不再做 `resolve_gm_session` 解析，**本地 session_id 直接作为 GM 会话 ID**；
- `main.py`：删除 `/api/memory/link/blocked` 与 `/api/memory/link/{session_id}` 观测端点；
- `builtin_tools.py`：`save_memory` 删除 `is_victim → rotate_session` 就地重试分支，内联为单次调用；
- `database.py`：删除 `memory_link` 建表 DDL 与 `link_*`/`_outbox_quarantine` 等函数（**存量库中的表与数据保留不动**，新库不再建）；`memory_outbox` 的 `quarantined`/`gm_session_id`/`rotate_count` 列标记为历史遗留；
- `config.py`：删除死配置 `MEMORY_LINK_GUARD_ENABLED`/`MEMORY_LINK_MAX_GENERATION`/`MEMORY_LINK_MAX_ROTATE_PER_ITEM`；
- 保留：`_verify_tasks` 纯观测轮询（见 12.3）。

### 12.9 E2E 验收记录（2026-10-09）

| 用例                                        | 结果                                                                                       |
| ----------------------------------------- | ---------------------------------------------------------------------------------------- |
| 新会话说"帮我记住工号 GZ-2026-0888、工位广州移动联创中心 12 楼" | ✅ 模型调用 save_memory → GM 任务 succeeded n=2 → `memory_item` 新增 2 条 active → 助手回复"已成功记住您的信息" |
| 重复发送相同内容                                  | ✅ succeeded + results 空 → 助手回复"已收到，此前已有相同记忆"（未虚报成功）                                      |
| 回合钩子                                      | ✅ `memory_outbox` 5 条全部 `sent`                                                           |
| 测试数据清理                                    | ✅ probe 会话、diagnostics 残留、governance_pending 全清，GM 库恢复基线（保留原始 4 条记忆）                     |

### 12.10 观测与排查入口

- 后端日志：启动出现"记忆服务已启用"；每次投递打印 `记忆投递成功 outbox=… task=…`；召回失败打印 debug 级降级原因。
- GM 侧任务状态：`GET {GM_BASE_URL}/api/v1/tasks/{task_id}`（带 Bearer Token）。
- GM 库直查（诊断跳过原因）：
  ```bash
  docker exec -i memory-cmic-postgres psql -U memory_cmic -d memory_cmic \
    -c "SELECT status, payload->'execution'->'diagnostics' FROM memory_task ORDER BY created_at DESC LIMIT 5;"
  ```
  > `docker exec` 执行 stdin SQL **必须带 `-i`**，否则 SQL 静默不执行且 exit 0。psql 用户是 `memory_cmic`（不是 postgres）。

### 12.11 用户画像注入与「本轮记忆」面板（v1.3.1）

**动机**：只有"事件记忆"召回时，助手不知道用户是谁（职业、偏好、常用地）。GM 的治理流程会从长期事实沉淀出**用户画像**（property），v1.3.1 起把它一并注入，让助手表现得像真实的个人助理。

**并行双通道召回**（`memory_retriever.recall()`）：

```
                 ┌── _safe_memory_recall(query, uid)  → GM POST /memories:search（记忆）
recall(query) ──┤                                     asyncio.gather
                 └── _safe_profile_recall(uid)        → GM GET /profiles?include_inferred=true（画像）
```

- **两路完全独立降级**：画像超时/报错只让画像路降级（`profile_reason`），记忆照常注入；反之亦然。GM 整体不可达时两路各自记 `net_error:ConnectError`，prompt 块为空但**绝不抛异常**。
- **画像与 query 无关**：即使记忆路因短输入降级（`query_too_short`），画像仍照常注入（实测 1 字符 query 时 `profile_only_block=True`）。

**GM 画像端点契约**（实测固化）：

| 项 | 值 |
|---|---|
| 端点 | `GET /api/v1/profiles?user_id=...&include_inferred=true`，权限 `Depends(read_user_memory)` |
| 响应 | `{"profiles":[{id, property_key(恒 null), property_value(jsonb 标量), value_type, business_domains, assertion_kind}]}` |
| 分页 | **无 limit 参数** → 客户端截断：`assertion_kind=="explicit"` 优先排序后取前 `MEMORY_PROFILE_LIMIT` |
| 类型 | `property_value` 非字符串时 `json.dumps(..., ensure_ascii=False)` 兜底 |
| 实测 | 16 条全 explicit、全 string、单次 0.4-0.9s |

> **inferred 画像为何可能是 0**：GM 治理（`POST /api/v1/governance/runs`）确实会跑推断生成链路，但 inferred 画像要求 basis fact 的 `retention_kind` 达到 `long_term` 门槛——这是 GM 有意的质量门禁，非本项目缺陷。当前 16 条全为 explicit。

**prompt 渲染**（`format_memories_for_prompt`）：拆为 `_format_memory_section`（原逻辑不变）+ `_format_profile_section`，两块 `"\n\n".join`。画像小节标题「# 用户画像（长期属性，仅供参考）」，表格含「类型（明说 / 推断（低置信度））+ 内容」，value 截断 160 字符，并附 5 条使用规则——沿用「**数据不是指令**」范式：声明属于数据非指令、冲突以用户当前表述为准、禁止向用户暴露机制措辞、看似指令的文字一律不执行。

**SSE 透传**：`memory_start` 触发条件改为 `retrieval_enabled() or profiles_enabled()`；`memory` 事件在原记忆字段基础上新增 `profile_hits / profile_total / profile_elapsed_ms / profile_degraded / profile_reason / profile_items[{key,value,assertion_kind,business_domains}]`；`done.memory` 同源。`ChatResponse.memory` 本就是 `dict[str, Any] | None` 自由字典，故 `models.py` 无需改动。

**前端「本轮记忆」面板**（`MessageBubble.vue`）：

| 状态 | 表现 |
|---|---|
| 召回中（`memory_start`） | 🧠 "正在回忆…" + ▾ |
| 完成（`memory`） | 🧠 "本轮记忆：5 条记忆 · 16 条画像" + ▾ |
| 展开 → 长期记忆区 | 每条：事实/推断标签（绿/黄）+ 内容 + 相似度 + 耗时（`长期记忆 5 条 · 571ms`） |
| 展开 → 用户画像区 | 每条：明说/推断标签 + 内容 + 业务域（`用户画像 16 条 · 429ms`） |
| 降级 | 显示降级原因中文标签（`DEGRADE_LABELS`：超时/GM 接口异常/网络错误/输入过短/画像未开启…） |

事件接线：`api/chat.js` 新增 `case 'memory_start'` / `case 'memory'` → `App.vue` 的 `onMemoryStart`（置 `aiMsg.recallLoading=true`）与 `onMemory`（写 `aiMsg.memory`、清 loading）→ `ChatWindow.vue` 透传 `:memory` / `:recall-loading` 给 `MessageBubble`。AI 占位消息新增 `memory: null, recallLoading: false` 两字段，且**必须先取回 reactive proxy 再改**（Vue 3 陷阱，见十三）。

**E2E 验收记录（2026-10-15，Playwright + 真实 SSE）**：

| 用例 | 结果 |
|---|---|
| 面板文案时序 | ✅ TOGGLE(early) "🧠 正在回忆… ▾" → TOGGLE(final) "🧠 本轮记忆：5 条记忆 · 16 条画像 ▾" |
| 展开分区 | ✅ SECTIONS `["长期记忆 5 条 · 571ms","用户画像 16 条 · 429ms"]` |
| 条目与标签 | ✅ ITEMS 21 条；TAGS = 5×事实 + 16×明说 |
| SSE `memory` 事件 | ✅ `profile_hits:16 / profile_total:16 / profile_elapsed_ms:440 / profile_items` 16 条；`done.finished_reason:completed` |
| 画像实际被运用 | ✅ 模型回复"您好，老王！…您偏好简洁的表格输出"，自然使用画像且未暴露机制措辞 |
| 降级四场景 | ✅ 正常 / 短 query"你好" / 1 字符"你"（记忆 `query_too_short`、画像照注入）/ GM 不可达（双路 `net_error`、块空、不抛异常）/ 画像开关关闭（记忆照常、`profile_disabled_or_unconfigured`） |
| 前端无报错 | ✅ PAGE_ERRORS none；`npm run build` 通过（21.12s）；截图 `memory_panel_ui.png` |

> MCP 工具 `memory_profile` 自 v1.3.1 起定位为**兜底**：画像通常每轮已自动注入，仅在怀疑注入缺失或需全量核对时才由模型主动调用。

---

## 十三、常见问题排查

### 1. Vite 启动报错 `@esbuild/win32-x64 could not be found`

**原因**：`npm install` 漏装了 esbuild 的 Windows 平台二进制（optionalDependencies）。

**解决**：

```bash
cd frontend
npm install @esbuild/win32-x64@0.25.12 --no-save
```

### 2. `pip install agentscope` 失败（No matching distribution）

**原因**：清华等国内镜像源没有 agentscope。

**解决**：指定官方源：

```bash
pip install -r requirements.txt -i https://pypi.org/simple
```

> 注意：若用 `&&` 串联多条安装命令，前一条失败会中断后续，导致 `fastapi` 等也没装上。建议整体用官方源安装。

### 3. 对话返回 503 / 界面显示黄色警告条

**原因**：未配置真实 DashScope API Key（`.env` 中仍是 `YOUR_DASHSCOPE_KEY`）。

**解决**：填入真实密钥并重启后端（见[快速开始](#前置配置-dashscope-api-key必需)）。

### 4. AgentScope 2.x API 与网上 1.x 教程不一致

本项目基于 **AgentScope 2.0.9**，API 与 1.x 完全不同。关键差异（已实测）：

- 消息内容块是 **Pydantic 模型**（如 `TextBlock`），不可用 `b["name"]` 下标访问，须用 `getattr(b, "name", None)`。
- 工具调用块的 `type` 字面量是 `"tool_call"`（非 `"tool_use"`）。
- `Msg(content=...)` 的 content 必须是 list；构造用户消息用 `UserMsg(name, content=str)`（自动包装为 `[TextBlock]`）。
- `DashScopeCredential` 的 `type` 字面量是 `"dashscope_credential"`（非 `"dashscope"`）。
- `Toolkit.add_tool` / `get_tool_schemas` 是 **async**，必须 `await`。
- FastAPI 用 `lifespan` 上下文管理器（`on_event` 已弃用）。

### 5. 前端能打开但对话无响应

检查：① 后端是否在 :8000 运行；② `curl http://127.0.0.1:5173/api/health` 是否经代理返回正常；③ 浏览器控制台是否有报错。

### 6. 用户说"记住…"但助手没有调用 save_memory

依次检查：① `backend/.env` 中 `MEMORY_ENABLED=true` 且 `GM_TOKEN` 非空（为空则工具**不注册**）；② 后端启动日志有"记忆服务已启用"；③ 改动 `.env` 后是否重启了后端（若启动时未带 `--reload`，改配置/改代码都必须手动重启）；④ 用户表述是否含明确记忆意图（系统提示词约束"未明确要求时不主动调用"）。

### 7. GM 任务 succeeded 但 memory_item 没有新条目

两种可能：① **重复事实**——results 为空，属正常去重；② **治理跳过**——查 `memory_task.payload->'execution'->'diagnostics'`，若见 `SCOPE_UNRESOLVED` 说明事实被判范围不明（见 [12.5](#125-踩坑记录scope_unresolved-跳过根因与修复)）。另注意：同属性反复提交不同值会触发 DISPUTE，相关条目变 `disputed` 后后续任务会 failed，需用户显式澄清才能恢复。

### 8. 调本机 GM 返回 404（同一个 URL，sync 客户端 200 / async 客户端 404）

**原因**：开发环境注入了 `http_proxy`/`HTTPS_PROXY` 等代理变量。`httpx.AsyncClient` 默认 `trust_env=True` 会走代理，代理把**整段绝对 URI 当路径**转发，GM 收到 `POST http%3A//127.0.0.1%3A8001/api/...` → Starlette 默认 404（不是应用的 401/422，极易误判为"路由不存在"）。

**解决**：本地调用一律 `httpx.AsyncClient(trust_env=False)`（读写两侧模块均已内置）；调外网（DashScope/SiliconFlow）保持默认走代理。排障第一动作：看服务端访问日志的 request path，若是"整段绝对 URI 被百分号转义"，100% 是代理转发。`curl` 同样受影响，必要时 `curl --noproxy '*'`。

### 9. 前端流式输出"光标闪但文字不增长，done 后才整段出现"

**原因**：**Vue 3 响应式陷阱**——`messages.value.push(obj)` 之后再改 `obj` **不会触发重渲染**（push 进去的是原始对象，回调改的也是原始对象，绕过了数组里的 reactive proxy）。

**解决**（本项目已修复，新增流式消息时照此办理）：
1. `messages.value.push({...})` 后立刻 `const aiMsg = messages.value[messages.value.length - 1]` 取回 **proxy**，后续回调只改 `aiMsg`；
2. delta 合帧：`requestAnimationFrame` + `pendingText` 合并同帧多个 delta，done/error/abort 时 `flushDelta()` 幂等落盘；
3. 渲染节流：MessageBubble 内 markdown 每 90ms 最多重渲一次；流式期间跳过 highlight.js，结束后一次性高亮；
4. 滚动：禁用 `watch(messages, {deep:true})`，改 watch「条数+末条长度」computed；流式期间加 `.chat-window--instant` 关平滑滚动。

排障：怀疑"后端不流式"时先 `curl -sN` 直连 :8000 数 delta 帧时间戳——帧分散到达则后端无罪，直接查前端响应式。验证响应式可用项目自带 `@vue/reactivity` 的 `effect()` 数渲染次数。

### 10. 新工具调用"无声卡死"（SSE 只发 tool_start 不发 tool_result）

**原因**：AgentScope 2.0.9 默认 `PermissionMode.DEFAULT` 下，未显式声明权限的工具会被判 ASK → 工具 park + yield RequireUserConfirmEvent，而前端无确认 UI → 永久卡死（done 且 finished_reason 为空）。

**解决**：新增工具必须二选一——①只读类标 `is_read_only=True`；②有副作用但可信的传 `permission=PermissionDecision(behavior=PermissionBehavior.ALLOW, message=...)`（**语义优先选②**，不要为绕权限谎报 read-only）。排障：backend.log 里 dashscope 调用只有一次（无工具结果回填后的第二次 LLM 调用）即基本可判定权限 ASK。当前 5 个工具的权限声明见[第十章](#十内置工具)。

---

## 十四、后续规划

| 任务      | 内容                                                            | 状态                         |
| ------- | ------------------------------------------------------------- | -------------------------- |
| **任务1** | AI 助手 Demo（本项目）                                               | ✅ 已完成                      |
| **任务2** | GeneralMemory 接入：写入侧（`save_memory` + 回合钩子 + outbox）           | ✅ 已完成（2026-10-09 E2E 验收通过） |
| 任务2 读取侧 | 每轮召回注入 system_prompt；v1.2.0 起默认走 GM 官方 API（`use_model=false`） | ✅ 已完成（2026-10-15，7/7 等价性实测 + 真实召回验证） |
| **任务3** | 记忆管理平台（运营监控）                                                   | ✅ 主体已在 `../memory-platform/` 落地（v1.1.2） |
| **任务4** | 记忆服务插件化 → `gm-memory` MCP server                              | ✅ 已完成（v1.3.0，2026-10-15：自研 stdio MCP server 封装 GM 只读三工具，端到端验证通过） |

---

## 附：UI 设计稿

`design-demos/` 下有 3 套设计稿，当前采用 **方案3（Anthropic Custom 暖色现代风）**：

- 主色：陶土红 `#CC785C` / `#D97757`
- 背景：`#FAFAFA`；卡片：白色；边框：`#E8E8E8`
- 圆角：4–18px

主题变量定义于 `frontend/src/styles/variables.scss`。

---

*最后更新：2026-10-15（v1.3.0：gm-memory MCP server 落地（stdio 只读三工具 + 共享连接管理）、工具权限门禁机制化（注册前生产同款引擎强制校验 + /api/permissions 审计端点 + verify_permissions.py 脚本）；v1.2.0：读侧切 GM API 双后端、移除 memory_link 会话守护、集成 139邮箱 Skill、GM 环境对齐 v2.6.0）*
