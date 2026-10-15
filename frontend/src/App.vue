<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import SessionSidebar from './components/SessionSidebar.vue'
import ChatWindow from './components/ChatWindow.vue'
import InputArea from './components/InputArea.vue'
import {
  getHealth,
  getSessions,
  getSessionHistory,
  deleteSession,
  renameSession,
  chatStream,
} from './api/chat'

// ------------------------------------------------------------------ 状态
const sessions = ref([])
const messages = ref([])
const currentSessionId = ref('')
const collapsed = ref(false)
const loading = ref(false)
const apiKeyConfigured = ref(true)
const backendOnline = ref(true)

// 续用标记：用户主动切回历史会话时，需告诉后端 resume=true 忽略闲置超时
const resumeFlag = ref(false)

let abortController = null

// ------------------------------------------------------------------ 流式 delta 合帧
// qwen-plus 流式每秒可产出数十个 delta，而每条 AI 消息渲染都要走
// marked 解析 + DOMPurify 消毒 + highlight.js 高亮，逐个 delta 同步刷新会明显掉帧。
// 因此把同一帧内到达的 delta 合并为一次写入（≈60fps），观感仍是逐字流出。
let streamTarget = null // 当前正在流式填充的消息（必须是 reactive proxy，见 onSend）
let pendingText = ''
let rafId = 0

function applyPending() {
  if (!streamTarget || !pendingText) return
  streamTarget.content += pendingText
  pendingText = ''
}

/** 取消待执行的 rAF 并立即写入残余文本（done / error / 用户停止时调用，幂等） */
function flushDelta() {
  if (rafId) {
    cancelAnimationFrame(rafId)
    rafId = 0
  }
  applyPending()
}

function onTextDelta(text) {
  if (!text) return
  pendingText += text
  if (!rafId) {
    rafId = requestAnimationFrame(() => {
      rafId = 0
      applyPending()
    })
  }
}

// ------------------------------------------------------------------ 工具函数
function nowTime() {
  const d = new Date()
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

async function loadSessions() {
  try {
    const data = await getSessions()
    sessions.value = data.sessions || data || []
  } catch (e) {
    console.error('加载会话列表失败', e)
  }
}

async function loadHealth() {
  try {
    const h = await getHealth()
    apiKeyConfigured.value = !!h.api_key_configured
    backendOnline.value = true
  } catch (e) {
    backendOnline.value = false
    console.error('后端未连接', e)
  }
}

// ------------------------------------------------------------------ 会话操作
function onNewChat() {
  if (loading.value) stopStreaming()
  currentSessionId.value = ''
  resumeFlag.value = false
  messages.value = []
}

async function onSelect(sessionId) {
  if (sessionId === currentSessionId.value) return
  if (loading.value) stopStreaming()

  currentSessionId.value = sessionId
  // 用户主动切回历史会话：续用，忽略 30 分钟超时
  resumeFlag.value = true
  messages.value = []

  try {
    const data = await getSessionHistory(sessionId)
    messages.value = (data.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
      timestamp: '',
      streaming: false,
      toolCalls: m.tool_calls || [],
      error: '',
    }))
  } catch (e) {
    ElMessage.error('加载历史消息失败')
    console.error(e)
  }
}

async function onDelete(sessionId) {
  try {
    await deleteSession(sessionId)
    sessions.value = sessions.value.filter((s) => s.session_id !== sessionId)
    if (sessionId === currentSessionId.value) onNewChat()
    ElMessage.success('已删除')
  } catch (e) {
    ElMessage.error('删除失败')
    console.error(e)
  }
}

async function onRename(sessionId, title) {
  try {
    await renameSession(sessionId, title)
    const s = sessions.value.find((x) => x.session_id === sessionId)
    if (s) s.title = title
  } catch (e) {
    ElMessage.error('重命名失败')
    console.error(e)
  }
}

// ------------------------------------------------------------------ 发送 / 流式
function stopStreaming() {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  // 把已收到但未渲染的残余文本落盘，避免用户点"停止"后丢字
  flushDelta()
  streamTarget = null
  loading.value = false
  // 结束当前流式气泡的 streaming 状态
  const last = messages.value[messages.value.length - 1]
  if (last && last.streaming) last.streaming = false
}

async function onSend(text) {
  const content = (text || '').trim()
  if (!content || loading.value) return

  // 1) 先插入用户消息
  messages.value.push({
    role: 'user',
    content,
    timestamp: nowTime(),
    streaming: false,
    toolCalls: [],
    error: '',
  })

  // 2) 插入占位的 AI 消息（流式填充）
  messages.value.push({
    role: 'assistant',
    content: '',
    timestamp: nowTime(),
    streaming: true,
    toolCalls: [],
    error: '',
    // 本轮记忆/画像召回明细（memory 事件到达后填充；recallLoading 供面板显示"正在回忆"）
    memory: null,
    recallLoading: false,
  })
  // 关键：必须取回数组里的 reactive proxy 再修改。
  // 若直接改 push 进去的原始对象，Vue 3 追踪不到，delta 不会触发重渲染
  // （表现为"不是流式"：光标一直闪、文字不增长，直到 done 才一次性出现）。
  const aiMsg = messages.value[messages.value.length - 1]

  loading.value = true
  abortController = new AbortController()
  streamTarget = aiMsg
  pendingText = ''

  try {
    await chatStream(
      {
        message: content,
        session_id: currentSessionId.value || undefined,
        resume: resumeFlag.value,
      },
      {
        onMeta: (ev) => {
          if (ev.session_id) currentSessionId.value = ev.session_id
          // meta 之后就进入本轮对话，resume 归位
          resumeFlag.value = false
        },
        onMemoryStart: () => {
          aiMsg.recallLoading = true
        },
        onMemory: (ev) => {
          // 完整召回明细（记忆 + 画像），供气泡下"本轮记忆"面板展示
          aiMsg.memory = ev
          aiMsg.recallLoading = false
        },
        onToolStart: (ev) => {
          flushDelta()
          aiMsg.toolCalls.push({ name: ev.name, state: '' })
        },
        onToolResult: (ev) => {
          const t = [...aiMsg.toolCalls].reverse().find((x) => x.name === ev.name && !x.state)
          if (t) t.state = ev.state || 'done'
        },
        onDelta: (ev) => {
          onTextDelta(ev.text)
        },
        onDone: (ev) => {
          flushDelta()
          // 兜底校正：done 事件带后端拼接的完整 reply，
          // 若流式过程中有任何 delta 丢失/被中断，这里一次性补齐。
          if (ev?.reply && ev.reply !== aiMsg.content) aiMsg.content = ev.reply
          aiMsg.streaming = false
          loading.value = false
          loadSessions()
        },
        onError: (msg) => {
          flushDelta()
          aiMsg.streaming = false
          aiMsg.error = msg || '对话失败'
          loading.value = false
        },
      },
      abortController.signal,
    )
  } catch (e) {
    if (e.name === 'AbortError') {
      flushDelta()
      aiMsg.streaming = false
    } else {
      flushDelta()
      aiMsg.streaming = false
      aiMsg.error = e.message || '请求失败'
      console.error(e)
    }
  } finally {
    flushDelta()
    streamTarget = null
    loading.value = false
    abortController = null
  }
}

// ------------------------------------------------------------------ 生命周期
onMounted(async () => {
  await Promise.all([loadHealth(), loadSessions()])
})
</script>

<template>
  <div class="app">
    <SessionSidebar
      :sessions="sessions"
      :current-session-id="currentSessionId"
      :collapsed="collapsed"
      @new="onNewChat"
      @select="onSelect"
      @rename="onRename"
      @delete="onDelete"
      @toggle-collapse="collapsed = !collapsed"
    />

    <main class="main">
      <header v-if="!backendOnline" class="offline-bar">
        无法连接后端服务（:8000），请确认已启动 <code>uvicorn app.main:app</code>
      </header>

      <ChatWindow
        :messages="messages"
        :loading="loading"
        :api-key-configured="apiKeyConfigured"
        @quick-send="onSend"
      />

      <InputArea
        :loading="loading"
        :disabled="!backendOnline"
        @send="onSend"
        @stop="stopStreaming"
      />
    </main>
  </div>
</template>

<style lang="scss" scoped>
.app {
  display: flex;
  height: 100%;
  width: 100%;
  overflow: hidden;
}

.main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.offline-bar {
  flex-shrink: 0;
  padding: 8px 20px;
  font-size: 13px;
  text-align: center;
  color: #a8071a;
  background: #fff1f0;
  border-bottom: 1px solid #ffccc7;

  code {
    padding: 1px 5px;
    background: rgba(0, 0, 0, 0.05);
    border-radius: 4px;
    font-family: 'SFMono-Regular', Consolas, monospace;
    font-size: 12px;
  }
}
</style>
