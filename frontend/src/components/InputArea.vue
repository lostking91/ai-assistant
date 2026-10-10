<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'

const props = defineProps({
  disabled: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
})

const emit = defineEmits(['send', 'stop'])

const text = ref('')
const textareaRef = ref(null)
const MAX_HEIGHT = 160

/** 自适应高度 */
function autoResize() {
  const el = textareaRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, MAX_HEIGHT)}px`
}

watch(text, () => nextTick(autoResize))

onMounted(() => {
  autoResize()
  textareaRef.value?.focus()
})

// 发送后重新聚焦，保证连续对话体验
watch(
  () => props.loading,
  (val) => {
    if (!val) nextTick(() => textareaRef.value?.focus())
  },
)

function send() {
  const value = text.value.trim()
  if (!value || props.loading) return
  emit('send', value)
  text.value = ''
  nextTick(autoResize)
}

function onKeydown(e) {
  // Enter 发送，Shift+Enter 换行；中文输入法组词时不触发
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

function clear() {
  text.value = ''
  nextTick(autoResize)
}

defineExpose({ clear })
</script>

<template>
  <div class="input-area">
    <div class="input-wrapper">
      <textarea
        ref="textareaRef"
        v-model="text"
        class="input-box"
        :placeholder="loading ? 'AI 正在思考…' : '输入消息…'"
        rows="1"
        :disabled="disabled"
        @keydown="onKeydown"
      />

      <button
        v-if="loading"
        class="action-btn action-btn--stop"
        title="停止生成"
        @click="emit('stop')"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
          <rect x="6" y="6" width="12" height="12" rx="2" />
        </svg>
      </button>

      <button
        v-else
        class="action-btn"
        :disabled="!text.trim() || disabled"
        title="发送"
        @click="send"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="22" y1="2" x2="11" y2="13" />
          <polygon points="22 2 15 22 11 13 2 9 22 2" />
        </svg>
      </button>
    </div>
    <div class="input-hint">按 Enter 发送，Shift + Enter 换行</div>
  </div>
</template>

<style lang="scss" scoped>
.input-area {
  background-color: var(--color-card);
  border-top: 1px solid var(--color-border);
  padding: 16px 24px 14px;
  flex-shrink: 0;
}

.input-wrapper {
  max-width: var(--content-max-width);
  margin: 0 auto;
  position: relative;
}

.input-box {
  width: 100%;
  min-height: 52px;
  max-height: 160px;
  padding: 15px 52px 15px 18px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  color: var(--color-text);
  background-color: var(--color-input-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  resize: none;
  outline: none;
  transition: border-color 0.2s, background-color 0.2s, box-shadow 0.2s;

  &:focus {
    background-color: var(--color-card);
    border-color: var(--color-primary);
    box-shadow: 0 0 0 3px var(--color-primary-soft);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.7;
  }

  &::placeholder {
    color: var(--color-text-hint);
  }
}

.action-btn {
  position: absolute;
  right: 10px;
  bottom: 8px;
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-light) 100%);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  box-shadow: var(--shadow-btn);
  transition: transform 0.15s, box-shadow 0.15s, opacity 0.15s;

  &:hover:not(:disabled) {
    transform: scale(1.08);
    box-shadow: 0 3px 10px rgba(204, 120, 92, 0.35);
  }

  &:active:not(:disabled) {
    transform: scale(0.96);
  }

  &:disabled {
    cursor: not-allowed;
    opacity: 0.45;
    box-shadow: none;
  }

  &--stop {
    background: #8c8c8c;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
  }
}

.input-hint {
  max-width: var(--content-max-width);
  margin: 10px auto 0;
  text-align: center;
  font-size: 12px;
  color: var(--color-text-hint);
}
</style>
