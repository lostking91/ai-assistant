<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import MessageBubble from './MessageBubble.vue'

const props = defineProps({
  // [{ role, content, timestamp, streaming, toolCalls, error }]
  messages: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  apiKeyConfigured: { type: Boolean, default: true },
})

const emit = defineEmits(['quick-send'])

const scrollRef = ref(null)
// 用户是否手动上滚（若上滚则不自动跟随底部）
const stickToBottom = ref(true)
// 是否有消息正在流式输出：流式时关闭平滑滚动，避免每帧重启动画造成抖动
const isStreaming = computed(() => props.messages.some((m) => m.streaming))

const suggestions = [
  { icon: '🕐', text: '现在几点了？' },
  { icon: '🧮', text: '帮我算一下 (128 * 7 + 91) / 3' },
  { icon: '🐍', text: '用 Python 生成前 20 个斐波那契数' },
  { icon: '📊', text: '用表格对比 SQLite 和 MySQL 的适用场景' },
]

function isNearBottom() {
  const el = scrollRef.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 120
}

function onScroll() {
  stickToBottom.value = isNearBottom()
}

function scrollToBottom(force = false) {
  if (!force && !stickToBottom.value) return
  nextTick(() => {
    const el = scrollRef.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 流式跟随：只观察「消息条数 + 最后一条的文本长度」，
// 替代对整个 messages 的 deep watch（deep 每帧都要遍历全部消息树，长会话下开销很大）
const msgCount = computed(() => props.messages.length)
// 末尾附 streaming 标记，使「内容长度不变但流式结束」这一跳变也能触发滚动
const lastMsgProgress = computed(() => {
  const last = props.messages[props.messages.length - 1]
  return last ? last.content.length + (last.streaming ? 1 : 0) : 0
})

watch([msgCount, lastMsgProgress], () => scrollToBottom())

// 切换会话导致消息数突变时强制到底
watch(msgCount, () => scrollToBottom(true))

defineExpose({ scrollToBottom })
</script>

<template>
  <div
    ref="scrollRef"
    class="chat-window"
    :class="{ 'chat-window--instant': isStreaming }"
    @scroll="onScroll"
  >
    <div class="chat-window__inner">
      <!-- 空状态欢迎页 -->
      <div v-if="!messages.length" class="welcome">
        <div class="welcome__logo">A</div>
        <h1 class="welcome__title">你好，我是 AI 助手</h1>
        <p class="welcome__subtitle">
          基于 AgentScope + qwen-plus，可以帮你查时间、做计算、跑 Python，并用 Markdown 清晰呈现。
        </p>

        <div v-if="!apiKeyConfigured" class="welcome__warn">
          <strong>⚠️ 尚未配置 DashScope API Key。</strong>
          请在 <code>backend/.env</code> 中填入真实密钥并重启后端后即可对话。
        </div>

        <div class="welcome__cards">
          <button
            v-for="(s, i) in suggestions"
            :key="i"
            class="suggest-card"
            @click="emit('quick-send', s.text)"
          >
            <span class="suggest-card__icon">{{ s.icon }}</span>
            <span class="suggest-card__text">{{ s.text }}</span>
          </button>
        </div>
      </div>

      <!-- 消息列表 -->
      <MessageBubble
        v-for="(m, i) in messages"
        :key="i"
        :role="m.role"
        :content="m.content"
        :timestamp="m.timestamp"
        :streaming="m.streaming"
        :tool-calls="m.toolCalls"
        :error="m.error"
        :memory="m.memory"
        :recall-loading="m.recallLoading"
      />

      <!-- 底部留白，避免最后一条被输入框遮挡 -->
      <div class="chat-window__tail" />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.chat-window {
  flex: 1;
  overflow-y: auto;
  scroll-behavior: smooth;
  background-color: var(--color-bg);

  // 流式输出时改为瞬时滚动：平滑动画会被每帧的滚动请求反复重启，导致视觉抖动
  &--instant {
    scroll-behavior: auto;
  }

  &__inner {
    max-width: var(--content-max-width);
    margin: 0 auto;
    padding: 28px 24px 8px;
  }

  &__tail {
    height: 12px;
  }
}

.welcome {
  padding: 48px 0 24px;
  text-align: center;
  animation: fadeIn 0.4s ease-out;

  &__logo {
    width: 56px;
    height: 56px;
    margin: 0 auto 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-lg);
    background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-light) 100%);
    color: #fff;
    font-size: 26px;
    font-weight: 700;
    box-shadow: var(--shadow-btn);
  }

  &__title {
    font-size: 24px;
    font-weight: 600;
    color: var(--color-text);
    margin-bottom: 10px;
  }

  &__subtitle {
    font-size: 14px;
    line-height: 1.7;
    color: var(--color-text-secondary);
    max-width: 460px;
    margin: 0 auto 20px;
  }

  &__warn {
    max-width: 480px;
    margin: 0 auto 24px;
    padding: 12px 16px;
    font-size: 13px;
    line-height: 1.7;
    text-align: left;
    color: #874d00;
    background: #fffbe6;
    border: 1px solid #ffe58f;
    border-radius: var(--radius-md);

    code {
      padding: 1px 5px;
      background: rgba(0, 0, 0, 0.05);
      border-radius: 4px;
      font-family: 'SFMono-Regular', Consolas, monospace;
      font-size: 12px;
    }
  }

  &__cards {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
    max-width: 600px;
    margin: 8px auto 0;
  }
}

.suggest-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 16px;
  text-align: left;
  background-color: var(--color-card);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;

  &:hover {
    border-color: var(--color-primary);
    box-shadow: var(--shadow-card);
    transform: translateY(-2px);
  }

  &__icon {
    font-size: 18px;
    flex-shrink: 0;
  }

  &__text {
    font-size: 13px;
    line-height: 1.5;
    color: var(--color-text-secondary);
  }
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
