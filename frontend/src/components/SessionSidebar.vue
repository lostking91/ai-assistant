<script setup>
import { computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

const props = defineProps({
  sessions: { type: Array, default: () => [] },
  currentSessionId: { type: String, default: '' },
  collapsed: { type: Boolean, default: false },
})

const emit = defineEmits(['new', 'select', 'rename', 'delete', 'toggle-collapse'])

/** 相对时间显示：今天显示时分，昨天显示"昨天"，更早显示日期 */
function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const now = new Date()
  const diffMs = now - d
  const sameDay = d.toDateString() === now.toDateString()

  const hhmm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  if (sameDay) return hhmm
  if (diffMs < 2 * 86400000) return '昨天'
  if (d.getFullYear() === now.getFullYear()) {
    return `${d.getMonth() + 1}月${d.getDate()}日`
  }
  return `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

const grouped = computed(() => {
  const now = new Date()
  const buckets = { 今天: [], 昨天: [], '7天内': [], 更早: [] }

  for (const s of props.sessions) {
    const d = new Date(s.updated_at)
    if (Number.isNaN(d.getTime())) {
      buckets['更早'].push(s)
      continue
    }
    const sameDay = d.toDateString() === now.toDateString()
    const yesterday = new Date(now)
    yesterday.setDate(now.getDate() - 1)
    const isYesterday = d.toDateString() === yesterday.toDateString()
    const withinWeek = now - d < 7 * 86400000

    if (sameDay) buckets['今天'].push(s)
    else if (isYesterday) buckets['昨天'].push(s)
    else if (withinWeek) buckets['7天内'].push(s)
    else buckets['更早'].push(s)
  }
  return Object.entries(buckets).filter(([, list]) => list.length > 0)
})

async function onDelete(session) {
  try {
    await ElMessageBox.confirm(
      `确定删除会话「${session.title}」吗？此操作不可恢复。`,
      '删除会话',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return // 用户取消
  }
  emit('delete', session.session_id)
}

async function onRename(session) {
  let value
  try {
    const res = await ElMessageBox.prompt('请输入新的会话名称', '重命名会话', {
      inputValue: session.title,
      confirmButtonText: '保存',
      cancelButtonText: '取消',
      inputValidator: (v) => (v && v.trim() ? true : '名称不能为空'),
    })
    value = res.value
  } catch {
    return
  }
  if (value && value.trim() && value.trim() !== session.title) {
    emit('rename', session.session_id, value.trim())
    ElMessage.success('已重命名')
  }
}
</script>

<template>
  <aside class="sidebar" :class="{ 'sidebar--collapsed': collapsed }">
    <div class="sidebar__head">
      <div class="sidebar__brand">
        <span class="sidebar__logo">A</span>
        <span v-show="!collapsed" class="sidebar__title">AI 助手</span>
      </div>
      <button class="icon-btn" :title="collapsed ? '展开侧栏' : '收起侧栏'" @click="emit('toggle-collapse')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path v-if="collapsed" d="M9 18l6-6-6-6" />
          <path v-else d="M15 18l-6-6 6-6" />
        </svg>
      </button>
    </div>

    <div class="sidebar__new">
      <button class="new-btn" @click="emit('new')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="12" y1="5" x2="12" y2="19" />
          <line x1="5" y1="12" x2="19" y2="12" />
        </svg>
        <span v-show="!collapsed">新建对话</span>
      </button>
    </div>

    <div v-show="!collapsed" class="sidebar__list">
      <div v-if="!sessions.length" class="sidebar__empty">
        还没有历史会话<br />开始你的第一次对话吧
      </div>

      <div v-for="[label, list] in grouped" :key="label" class="group">
        <div class="group__label">{{ label }}</div>
        <div
          v-for="s in list"
          :key="s.session_id"
          class="item"
          :class="{ 'item--active': s.session_id === currentSessionId }"
          @click="emit('select', s.session_id)"
        >
          <div class="item__main">
            <div class="item__title" :title="s.title">{{ s.title }}</div>
            <div class="item__time">{{ formatTime(s.updated_at) }}</div>
          </div>
          <div class="item__actions" @click.stop>
            <button class="icon-btn" title="重命名" @click="onRename(s)">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M12 20h9" />
                <path d="M16.5 3.5a2.12 2.12 0 013 3L7 19l-4 1 1-4z" />
              </svg>
            </button>
            <button class="icon-btn icon-btn--danger" title="删除" @click="onDelete(s)">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="3 6 5 6 21 6" />
                <path d="M19 6l-1 14H6L5 6" />
                <path d="M10 11v6M14 11v6" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>

    <div v-show="!collapsed" class="sidebar__foot">
      闲置超过 30 分钟的会话将自动开启新对话
    </div>
  </aside>
</template>

<style lang="scss" scoped>
.sidebar {
  width: var(--sidebar-width);
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  background-color: var(--color-card);
  border-right: 1px solid var(--color-border);
  transition: width 0.2s ease;
  overflow: hidden;

  &--collapsed {
    width: 60px;
  }

  &__head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 12px 10px;
    border-bottom: 1px solid var(--color-border-light);
  }

  &__brand {
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
  }

  &__logo {
    width: 30px;
    height: 30px;
    flex-shrink: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: var(--radius-md);
    background: linear-gradient(135deg, var(--color-primary) 0%, var(--color-primary-light) 100%);
    color: #fff;
    font-weight: 700;
    font-size: 15px;
  }

  &__title {
    font-size: 15px;
    font-weight: 600;
    color: var(--color-text);
    white-space: nowrap;
  }

  &__new {
    padding: 12px;
  }

  &__list {
    flex: 1;
    overflow-y: auto;
    padding: 0 8px 8px;
  }

  &__empty {
    padding: 32px 14px;
    text-align: center;
    font-size: 12.5px;
    line-height: 1.9;
    color: var(--color-text-hint);
  }

  &__foot {
    padding: 10px 14px;
    font-size: 11px;
    line-height: 1.6;
    color: var(--color-text-hint);
    border-top: 1px solid var(--color-border-light);
    text-align: center;
  }
}

.new-btn {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 9px 12px;
  font-size: 13.5px;
  font-weight: 500;
  color: #fff;
  background-color: var(--color-primary);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background-color 0.2s;
  white-space: nowrap;

  &:hover {
    background-color: var(--color-primary-hover);
  }
}

.group {
  margin-bottom: 14px;

  &__label {
    padding: 6px 8px 5px;
    font-size: 11px;
    font-weight: 600;
    color: var(--color-text-hint);
    letter-spacing: 0.3px;
  }
}

.item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px 8px 10px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background-color 0.15s;

  &:hover {
    background-color: var(--color-input-bg);

    .item__actions {
      opacity: 1;
    }
  }

  &--active {
    background-color: var(--color-primary-soft);

    .item__title {
      color: var(--color-primary-hover);
      font-weight: 600;
    }
  }

  &__main {
    flex: 1;
    min-width: 0;
  }

  &__title {
    font-size: 13px;
    color: var(--color-text);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__time {
    margin-top: 2px;
    font-size: 11px;
    color: var(--color-text-hint);
  }

  &__actions {
    display: flex;
    gap: 2px;
    flex-shrink: 0;
    opacity: 0;
    transition: opacity 0.15s;
  }
}

.icon-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-hint);
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  transition: background-color 0.15s, color 0.15s;
  flex-shrink: 0;

  &:hover {
    background-color: rgba(0, 0, 0, 0.06);
    color: var(--color-text-secondary);
  }

  &--danger:hover {
    background-color: #fff1f0;
    color: #a8071a;
  }
}
</style>
