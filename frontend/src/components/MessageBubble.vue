<script setup>
import { computed, nextTick, onMounted, onUnmounted, onUpdated, ref, watch } from 'vue'
import { renderMarkdown, highlightStreaming } from '@/utils/markdown'

const props = defineProps({
  role: { type: String, required: true }, // 'user' | 'assistant'
  content: { type: String, default: '' },
  timestamp: { type: String, default: '' },
  streaming: { type: Boolean, default: false },
  toolCalls: { type: Array, default: () => [] },
  error: { type: String, default: '' },
})

const bodyRef = ref(null)

const isUser = computed(() => props.role === 'user')

// 工具名 / 状态中文映射
const TOOL_LABELS = {
  get_current_time: '获取时间',
  calculator: '计算器',
  python_interpreter: 'Python',
  save_memory: '保存记忆',
}
const STATE_LABELS = {
  success: '完成',
  succeeded: '完成',
  done: '完成',
  error: '失败',
  failed: '失败',
}
function toolLabel(name) {
  return TOOL_LABELS[name] || name
}
function stateLabel(state) {
  if (!state) return '调用中'
  return STATE_LABELS[String(state).toLowerCase()] || state
}

// ---- Markdown 渲染节流 ----
// 流式期间 content 每帧都在变，而 renderMarkdown 要跑 marked 解析 + DOMPurify 消毒，
// 成本随文本长度线性增长；逐帧全量重渲染会让长回复变成 O(n²) 并明显掉帧。
// 因此流式中最多每 RENDER_INTERVAL_MS 重渲一次，定稿时立即渲染最终内容。
const RENDER_INTERVAL_MS = 90
const throttled = ref(props.content)
let lastRender = 0
let timer = null

watch(
  () => props.content,
  (val) => {
    if (!props.streaming) {
      // 定稿（含历史消息、done 校正）：立即同步，保证最终内容一字不差
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      throttled.value = val
      return
    }
    const now = Date.now()
    const wait = RENDER_INTERVAL_MS - (now - lastRender)
    if (wait <= 0) {
      lastRender = now
      throttled.value = val
    } else if (!timer) {
      timer = setTimeout(() => {
        timer = null
        lastRender = Date.now()
        throttled.value = props.content
      }, wait)
    }
  },
)

watch(
  () => props.streaming,
  (streaming) => {
    if (!streaming) {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      lastRender = 0
      throttled.value = props.content
    }
  },
)

onUnmounted(() => {
  if (timer) clearTimeout(timer)
})

// 用户消息按纯文本渲染，AI 消息渲染 Markdown
const html = computed(() => (isUser.value ? '' : renderMarkdown(throttled.value)))

function highlight() {
  // 流式过程中跳过代码高亮：未闭合的代码块每帧全量重算，既昂贵又会闪烁；
  // 等 streaming 变 false 后一次性高亮即可。
  if (props.streaming) return
  nextTick(() => highlightStreaming(bodyRef.value))
}

onMounted(highlight)
onUpdated(highlight)
// 渲染内容变化 / 流式结束（内容已定稿）时补高亮
watch(throttled, highlight)
watch(() => props.streaming, highlight)
</script>

<template>
  <div class="msg" :class="isUser ? 'msg--user' : 'msg--ai'">
    <div v-if="timestamp" class="msg__time">{{ timestamp }}</div>

    <div class="msg__row">
      <div v-if="!isUser" class="msg__avatar">A</div>

      <div class="msg__col">
        <!-- 工具调用状态 -->
        <div v-if="!isUser && toolCalls.length" class="msg__tools">
          <div v-for="(t, i) in toolCalls" :key="i" class="tool-chip">
            <span class="tool-chip__dot" :class="{ 'is-done': t.state }" />
            <span class="tool-chip__name">{{ toolLabel(t.name) }}</span>
            <span class="tool-chip__state">{{ stateLabel(t.state) }}</span>
          </div>
        </div>

        <!-- 消息气泡 -->
        <div class="msg__bubble">
          <span v-if="isUser">{{ content }}</span>
          <div v-else ref="bodyRef" class="md-body" v-html="html" />
          <span v-if="!isUser && streaming && !content" class="msg__thinking">
            <i /><i /><i />
          </span>
          <span v-if="!isUser && streaming && content" class="msg__caret" />
        </div>

        <!-- 错误提示 -->
        <div v-if="error" class="msg__error">
          <strong>出错了：</strong>{{ error }}
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.msg {
  margin-bottom: 28px;
  animation: fadeIn 0.25s ease-out;

  &__time {
    font-size: 11px;
    color: var(--color-text-hint);
    margin-bottom: 10px;
    font-weight: 500;
  }

  &__row {
    display: flex;
    align-items: flex-start;
  }

  // 关键：flex:1 让列获得确定宽度，气泡的百分比 max-width 才有参照，
  // 否则收缩包裹（shrink-to-fit）容器会让百分比失效、气泡被压窄
  &__col {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
    min-width: 0;
  }

  &__avatar {
    width: 32px;
    height: 32px;
    flex-shrink: 0;
    margin-right: 12px;
    border-radius: 50%;
    background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-light) 100%);
    color: #fff;
    font-size: 14px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  &__bubble {
    padding: 14px 18px;
    font-size: 14px;
    line-height: 1.75;
    word-break: break-word;
    position: relative;
  }

  // 用户消息：右对齐赤陶橙气泡
  &--user {
    .msg__time {
      text-align: right;
    }

    .msg__row {
      justify-content: flex-end;
    }

    // 列内右对齐，气泡按内容自适应、上限 75%（列宽已确定，百分比生效）
    .msg__col {
      align-items: flex-end;
    }

    .msg__bubble {
      max-width: min(75%, 560px);
      background-color: var(--color-primary);
      color: #fff;
      border-radius: var(--radius-pill) var(--radius-pill) 4px var(--radius-pill);
      box-shadow: var(--shadow-bubble);
      white-space: pre-wrap;
    }
  }

  // AI 消息：左对齐白色卡片
  &--ai {
    // 气泡按内容自适应宽度，但不超过列宽；代码块在气泡内横向滚动不溢出
    .msg__bubble {
      width: fit-content;
      max-width: 100%;
      background-color: var(--color-card);
      color: var(--color-text);
      border: 1px solid var(--color-border);
      border-radius: var(--radius-pill) var(--radius-pill) var(--radius-pill) 4px;
      box-shadow: var(--shadow-card);
    }
  }

  &__error {
    margin-top: 8px;
    padding: 10px 14px;
    font-size: 13px;
    color: #a8071a;
    background: #fff1f0;
    border: 1px solid #ffccc7;
    border-radius: var(--radius-md);
  }

  // 工具调用 chip
  &__tools {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 8px;
  }

  // 流式光标
  &__caret {
    display: inline-block;
    width: 2px;
    height: 1em;
    margin-left: 2px;
    vertical-align: text-bottom;
    background: var(--color-primary);
    animation: blink 1s step-end infinite;
  }

  // 思考中的三点动画
  &__thinking {
    display: inline-flex;
    gap: 4px;
    align-items: center;

    i {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--color-primary);
      opacity: 0.35;
      animation: pulse 1.2s infinite ease-in-out;

      &:nth-child(2) {
        animation-delay: 0.2s;
      }
      &:nth-child(3) {
        animation-delay: 0.4s;
      }
    }
  }
}

.tool-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 10px;
  font-size: 12px;
  color: var(--color-text-secondary);
  background: var(--color-input-bg);
  border: 1px solid var(--color-border);
  border-radius: 10px;

  &__dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--color-primary);
    animation: pulse 1s infinite;

    &.is-done {
      background: #52c41a;
      animation: none;
    }
  }

  &__name {
    font-family: 'SFMono-Regular', Consolas, monospace;
    color: var(--color-primary);
  }

  &__state {
    color: var(--color-text-hint);
  }
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(8px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes blink {
  50% {
    opacity: 0;
  }
}

@keyframes pulse {
  0%,
  100% {
    opacity: 0.3;
  }
  50% {
    opacity: 1;
  }
}
</style>

<style lang="scss">
// Markdown 正文样式：作用于 v-html 内容，故不能 scoped
.md-body {
  p {
    margin: 0 0 10px;
    &:last-child {
      margin-bottom: 0;
    }
  }

  h1,
  h2,
  h3,
  h4 {
    margin: 16px 0 8px;
    font-weight: 600;
    line-height: 1.4;
    &:first-child {
      margin-top: 0;
    }
  }
  h1 { font-size: 19px; }
  h2 { font-size: 17px; }
  h3 { font-size: 15px; }

  ul,
  ol {
    margin: 8px 0 10px;
    padding-left: 22px;
  }
  li {
    margin-bottom: 4px;
  }

  a {
    color: var(--color-primary);
    text-decoration: none;
    &:hover {
      text-decoration: underline;
    }
  }

  blockquote {
    margin: 10px 0;
    padding: 6px 14px;
    color: var(--color-text-secondary);
    border-left: 3px solid var(--color-primary);
    background: var(--color-input-bg);
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  }

  code {
    font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace;
    font-size: 13px;
  }

  // 行内代码
  :not(pre) > code {
    padding: 2px 5px;
    background: rgba(204, 120, 92, 0.1);
    color: var(--color-primary-hover);
    border-radius: 4px;
  }

  pre {
    margin: 10px 0;
    padding: 14px 16px;
    overflow-x: auto;
    background: #2d2a26;
    border-radius: var(--radius-md);

    code {
      color: #f5f0e8;
      background: transparent;
      padding: 0;
      font-size: 12.5px;
      line-height: 1.6;
    }
  }

  table {
    width: 100%;
    margin: 10px 0;
    border-collapse: collapse;
    font-size: 13px;
    display: block;
    overflow-x: auto;

    th,
    td {
      padding: 7px 12px;
      border: 1px solid var(--color-border);
      text-align: left;
    }

    th {
      background: var(--color-input-bg);
      font-weight: 600;
      color: var(--color-text);
    }

    tr:nth-child(even) td {
      background: #fcfcfc;
    }
  }

  hr {
    margin: 14px 0;
    border: none;
    border-top: 1px solid var(--color-border);
  }

  img {
    max-width: 100%;
    border-radius: var(--radius-md);
  }
}
</style>
