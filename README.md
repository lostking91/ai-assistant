# AI 助手 Demo（ai-assistant）

基于 **AgentScope 2.x + FastAPI + Vue 3** 的对话式 AI 助手。后端运行 ReAct 智能体（通义千问 `qwen-plus`），前端为暖色现代风格（方案3 · Anthropic Custom）的聊天界面，支持流式输出、工具调用、Markdown 渲染、会话持久化与历史会话管理。

> 本项目是「记忆服务集成」系列任务的载体：**任务1（AI 助手 Demo）已完成**；**任务2（GeneralMemory 写入侧接入）已完成**——新增 `save_memory` 工具 + 回合结束钩子两条写入路径，检索/召回按决策延后。详见[十二、GeneralMemory 记忆接入](#十二generalmemory-记忆接入任务2)与文末[后续规划](#十四后续规划)。

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
- [十二、GeneralMemory 记忆接入（任务2）](#十二generalmemory-记忆接入任务2)
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
└─────────────────────┘                             │   ├─ Toolkit（3+1 工具）  │
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
                                                     │  API :8001 + Worker(add)  │
                                                     │  PostgreSQL :5433 (Docker)│
                                                     │  memories:add / tasks 查询 │
                                                     └──────────────────────────┘
```

- **前后端独立部署**，通过 REST + SSE 通信。
- 前端开发服务器用 **Vite 代理**把 `/api` 转发到 `http://127.0.0.1:8000`，规避跨域并让 SSE 正常工作。
- 后端把 AgentScope 的 **`AgentState` 整体序列化**存入 SQLite，保留完整上下文与工具调用轨迹。
- **记忆写入侧（任务2）**：两条路径写 GM——① `save_memory` 工具（用户明确要求记住，同步等结果）；② 回合结束钩子（每轮 user+assistant 消息，经本地 `memory_outbox` 表落库后异步投递，重启可重发）。详见[第十二章](#十二generalmemory-记忆接入任务2)。

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
│       ├── main.py               # FastAPI 入口，路由与 lifespan（启动时 flush outbox）
│       ├── config.py             # 配置（读取 .env，含 GM 记忆配置项）
│       ├── models.py             # 请求/响应 Pydantic 模型
│       ├── agent.py              # AgentScope 智能体封装、会话管理、流式对话、回合记忆钩子
│       ├── database.py           # SQLite 持久化（sessions + memory_outbox）
│       ├── memory_client.py      # GM 写入侧客户端（契约适配、outbox 投递、结果归纳）
│       └── tools/
│           ├── __init__.py
│           └── builtin_tools.py  # 4 个内置工具实现（含 save_memory）
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
│           ├── MessageBubble.vue   # 消息气泡（Markdown、工具调用、流式光标）
│           └── InputArea.vue       # 输入框（自适应高度、Enter 发送）
└── design-demos/                 # UI 设计稿（参考用）
    ├── 1-gallery-dark.html       # 方案1
    ├── 2-claude-design.html      # 方案2
    └── 3-anthropic-custom.html   # 方案3 ★当前采用
```

---

## 三、功能清单

### 已实现（任务1 + 任务2）

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

### 待实现（后续任务）

| 功能                             | 所属任务      | 状态      |
| ------------------------------ | --------- | ------- |
| `search_memory` 记忆检索工具（检索/召回侧） | 任务2（延后部分） | ⏳ 按决策延后 |
| 记忆管理平台                         | 任务3       | ⏳       |
| 记忆服务插件化                        | 任务4       | ⏳       |

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
| httpx             | ≥0.27   | GM 记忆服务 HTTP 客户端（任务2 新增） |

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

### 记忆服务配置（任务2 新增）

| 变量                   | 默认值                     | 说明                                                                         |
| -------------------- | ----------------------- | -------------------------------------------------------------------------- |
| `MEMORY_ENABLED`     | `true`                  | 记忆写入总开关。关闭后 `save_memory` 不注册、回合钩子不投递                                      |
| `GM_BASE_URL`        | `http://127.0.0.1:8001` | GeneralMemory API 地址                                                       |
| `GM_TOKEN`           | （空）                     | ★GM Bearer Token，**为空则记忆功能整体禁用**（`memory_enabled()` 要求 token/base_url 均非空） |
| `GM_SOURCE_SYSTEM`   | `ai_assistant`          | GM 侧的来源系统标识                                                                |
| `GM_USER_ID`         | `gm_user_lw`            | GM 租户内用户 ID（当前租户 `cmic-yun`）                                               |
| `GM_WAIT_TIMEOUT_MS` | `15000`                 | `save_memory` 同步等待抽取结果的超时（毫秒），HTTP 超时在此基础上 +10s                            |


> `has_api_key` 判定逻辑：密钥非空且不以 `YOUR_` 开头才视为已配置。

---

## 八、API 接口文档

所有接口以 `/api` 为前缀。后端启动后可访问 <http://127.0.0.1:8000/docs> 查看自动生成的 Swagger 文档。

### 接口一览

| 方法     | 路径                            | 说明                        |
| ------ | ----------------------------- | ------------------------- |
| GET    | `/api/health`                 | 健康检查（含模型、API Key 状态、工具列表） |
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
  "tools": ["get_current_time", "calculator", "python_interpreter"]
}
```

> 健康检查里的 `tools` 恒为 3 个基础工具：`save_memory` 只在有会话上下文（session_id）时才注册进 Toolkit，健康检查不绑定会话，属预期行为。

**SessionItem（列表项）**：`{session_id, title, created_at, updated_at}`

**SessionHistory**：`{session_id, title, messages: [MessageItem]}`

**MessageItem**：`{role: "user"|"assistant", content, tool_calls: [], created_at}`

### SSE 流式事件（`/api/chat/stream`）

响应为 `text/event-stream`，每条报文形如 `data: {JSON}\n\n`，流结束时追加 `event: end`。事件类型：

| type          | 载荷字段                                                           | 说明          |
| ------------- | -------------------------------------------------------------- | ----------- |
| `meta`        | `session_id, is_new_session, title`                            | 本轮元信息（最先发出） |
| `tool_start`  | `name`                                                         | 开始调用某工具     |
| `tool_result` | `name, state`                                                  | 工具调用结束及状态   |
| `delta`       | `text`                                                         | 文本增量（逐字）    |
| `done`        | `reply, tool_calls, session_id, title, finished_reason, error` | 本轮完成        |
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
| `session_id`                | TEXT                     | 所属会话                                      |
| `idempotency_key`           | TEXT UNIQUE              | GM 幂等键（UUID hex，1-128 ASCII）              |
| `payload_json`              | TEXT                     | AddRequest 完整载荷（含 messages）               |
| `status`                    | TEXT                     | `pending` / `sending` / `sent` / `failed` |
| `attempts`                  | INTEGER                  | 已尝试次数                                     |
| `task_id`                   | TEXT                     | 投递成功后 GM 返回的任务 ID                         |
| `last_error`                | TEXT                     | 最近一次失败原因                                  |
| `created_at` / `updated_at` | TEXT                     | 时间戳                                       |

- 索引：`idx_outbox_status(status, id)`，`flush_outbox` 按状态扫描待发件。

---

## 十、内置工具

由 `backend/app/tools/builtin_tools.py` 实现（AgentScope 2.x 无内置时间/计算/解释器工具，故自研）。工具函数返回 `ToolChunk`，Google 风格 docstring 自动生成 JSON schema。

| 工具                   | 参数                | 说明                                            | 安全设计                                            |
| -------------------- | ----------------- | --------------------------------------------- | ----------------------------------------------- |
| `get_current_time`   | 无                 | 获取当前日期时间（东八区，含星期）                             | 只读                                              |
| `calculator`         | `expression: str` | 计算数学表达式，支持四则、幂、取余及 `sqrt/log/sin/cos/round` 等 | **AST 白名单**求值，杜绝 `eval` 注入                      |
| `python_interpreter` | `code: str`       | 受限沙箱执行 Python 代码，返回 stdout/stderr/异常          | **禁用高危内建**（`open/exec/eval/__import__/input` 等） |
| `save_memory`        | `content: str`    | 把用户明确要求记住的信息同步保存到 GM 长期记忆（任务2 新增）             | 仅在**有会话上下文且记忆功能启用**时注册；标 `is_read_only` 走权限快速通道 |

**calculator 示例**：`"2 + 3 * 4"` → `14`；`"sqrt(144)"` → `12`

**python_interpreter 示例**：`print(sum(range(10)))` → `标准输出：45`

**save_memory 说明**：系统提示词约束——仅当用户明确表达"记住/记一下/以后记得"等意图时调用；content 须是完整、独立可理解的事实陈述；一次只保存一个主题。工具返回值按 GM 任务结果语义区分"保存成功/重复未新增/处理中/失败"，并明确指示模型**不得虚报保存成功**。详见[第十二章](#十二generalmemory-记忆接入任务2)。

> `search_memory` 检索工具按决策延后（写入侧优先）。

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

---

## 十二、GeneralMemory 记忆接入（任务2）

> 决策范围：**仅写入侧**（`save_memory` 工具 + 回合钩子），检索/召回（`search_memory`）按用户决策延后。GM 跑 **8001** 端口，AI 助手保持 **8000**；租户 `cmic-yun`，`user_id=gm_user_lw`；回合钩子仅提交 **user + assistant 两条消息**（不含工具调用中间消息）。

### 12.1 GM 环境启动（三件套）

GM 源码位于同级目录 `../GeneralMemory/`（当前 v2.4.0，只读同步上游）。**完整启动/健康检查/升级流程见 [`../GeneralMemory/README.md`](../GeneralMemory/README.md)**，此处仅列启用记忆所需的最小三件套（Postgres + API + add Worker）：

**① PostgreSQL 容器**（数据持久化，端口 5433 避开本机默认 5432）：

```bash
docker start memory-cmic-postgres
# 若容器不存在，首次创建（凭证来自 GM 目录 .env 的 MEMORY_POSTGRES_*）：
# docker run -d --name memory-cmic-postgres -p 5433:5432 \
#   -e POSTGRES_DB=memory_cmic -e POSTGRES_USER=memory_cmic \
#   -e POSTGRES_PASSWORD=memory_cmic_local_dev postgres:16
```

**② GM API**（:8001，Git Bash；GM 无 python-dotenv，必须先 source .env 注入环境变量）：

```bash
cd ../GeneralMemory
set -a && . ./.env && set +a
./.venv/Scripts/python.exe -m uvicorn memory_cmic.api:create_app \
  --factory --host 127.0.0.1 --port 8001
```

**③ GM Worker**（add 角色，负责实际抽取/治理；如需画像/巩固另启 `maintenance`/`consolidation`/`profile` 角色）：

```bash
cd ../GeneralMemory
set -a && . ./.env && set +a
./.venv/Scripts/memory-cmic-worker.exe --role add
```

验证：`curl -o /dev/null -w "%{http_code}" http://127.0.0.1:8001/docs` 返回 200。

### 12.2 后端配置

`backend/.env` 增加记忆配置块（详见[第七章](#七配置说明env)）：

```ini
MEMORY_ENABLED=true
GM_BASE_URL=http://127.0.0.1:8001
GM_TOKEN=<GM 签发的 Bearer Token>
GM_SOURCE_SYSTEM=ai_assistant
GM_USER_ID=gm_user_lw
GM_WAIT_TIMEOUT_MS=15000
```

`GM_TOKEN` 为空时记忆功能整体静默禁用（`save_memory` 不注册、钩子不投递），助手其余功能不受影响。

### 12.3 两条写入路径

| 路径                   | 触发                | 消息内容                       | 投递方式                                                  | 结果语义                                             |
| -------------------- | ----------------- | -------------------------- | ----------------------------------------------------- | ------------------------------------------------ |
| **① save_memory 工具** | 用户明确说"记住…"，模型主动调用 | 单条 user 消息（套范围声明模板，见 12.5） | `wait_for_result=true` **同步**等 GM 抽取终态（15s 超时）        | 按任务状态如实回复（见 12.4）                                |
| **② 回合结束钩子**         | 每轮对话结束（流式/非流式都挂钩） | user + assistant 两条消息      | 先落 `memory_outbox` 表，再 **fire-and-forget** 异步投递，不阻塞响应 | 投递失败自动重试（0.5s/1s/2s 退避，最多 3 次），重启后 lifespan 自动补发 |

实现位置：`app/memory_client.py`（客户端+outbox）、`app/tools/builtin_tools.py`（工具）、`app/agent.py`（钩子挂载点）。

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

### 12.6 E2E 验收记录（2026-10-09）

| 用例                                        | 结果                                                                                       |
| ----------------------------------------- | ---------------------------------------------------------------------------------------- |
| 新会话说"帮我记住工号 GZ-2026-0888、工位广州移动联创中心 12 楼" | ✅ 模型调用 save_memory → GM 任务 succeeded n=2 → `memory_item` 新增 2 条 active → 助手回复"已成功记住您的信息" |
| 重复发送相同内容                                  | ✅ succeeded + results 空 → 助手回复"已收到，此前已有相同记忆"（未虚报成功）                                      |
| 回合钩子                                      | ✅ `memory_outbox` 5 条全部 `sent`                                                           |
| 测试数据清理                                    | ✅ probe 会话、diagnostics 残留、governance_pending 全清，GM 库恢复基线（保留原始 4 条记忆）                     |

### 12.7 观测与排查入口

- 后端日志：启动出现"记忆服务已启用"；每次投递打印 `记忆投递成功 outbox=… task=…`。
- GM 侧任务状态：`GET {GM_BASE_URL}/api/v1/tasks/{task_id}`（带 Bearer Token）。
- GM 库直查（诊断跳过原因）：
  ```bash
  docker exec -i memory-cmic-postgres psql -U memory_cmic -d memory_cmic \
    -c "SELECT status, payload->'execution'->'diagnostics' FROM memory_task ORDER BY created_at DESC LIMIT 5;"
  ```
  > `docker exec` 执行 stdin SQL **必须带 `-i`**，否则 SQL 静默不执行且 exit 0。

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

---

## 十四、后续规划

| 任务      | 内容                                                            | 状态                         |
| ------- | ------------------------------------------------------------- | -------------------------- |
| **任务1** | AI 助手 Demo（本项目）                                               | ✅ 已完成                      |
| **任务2** | GeneralMemory **写入侧**接入：`save_memory` 工具 + 回合钩子 + outbox 可靠投递 | ✅ 已完成（2026-10-09 E2E 验收通过） |
| 任务2 延后项 | 检索/召回侧（`search_memory` 工具、意图化检索）                              | ⏳ 按决策延后                    |
| **任务3** | 记忆管理平台（百炼 UI 复刻）                                              | ⏳ 待开始                      |
| **任务4** | 记忆服务插件化                                                       | ⏳ 待开始                      |

---

## 附：UI 设计稿

`design-demos/` 下有 3 套设计稿，当前采用 **方案3（Anthropic Custom 暖色现代风）**：

- 主色：陶土红 `#CC785C` / `#D97757`
- 背景：`#FAFAFA`；卡片：白色；边框：`#E8E8E8`
- 圆角：4–18px

主题变量定义于 `frontend/src/styles/variables.scss`。

---

*最后更新：2026-10-10（第六章补充实际后台启动命令与端口拓扑；12.1 GM 路径更新为 `../GeneralMemory/`，对齐 v2.4.0）*
