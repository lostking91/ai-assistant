// 后端 API 封装
//
// 说明：流式接口 POST /api/chat/stream 返回 SSE，
// 浏览器原生 EventSource 只支持 GET，因此用 fetch + ReadableStream 手动解析。

const BASE = '' // 走 vite 代理，无需前缀

/** 健康检查 */
export async function getHealth() {
  const res = await fetch(`${BASE}/api/health`)
  if (!res.ok) throw new Error(`健康检查失败：HTTP ${res.status}`)
  return res.json()
}

/** 会话列表 */
export async function getSessions(limit = 50) {
  const res = await fetch(`${BASE}/api/sessions?limit=${limit}`)
  if (!res.ok) throw new Error(`获取会话列表失败：HTTP ${res.status}`)
  return res.json()
}

/** 某会话的历史消息 */
export async function getSessionHistory(sessionId) {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}/history`)
  if (!res.ok) throw new Error(`获取历史消息失败：HTTP ${res.status}`)
  return res.json()
}

/** 删除会话 */
export async function deleteSession(sessionId) {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`删除会话失败：HTTP ${res.status}`)
  return res.json()
}

/** 重命名会话 */
export async function renameSession(sessionId, title) {
  const res = await fetch(`${BASE}/api/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  })
  if (!res.ok) throw new Error(`重命名失败：HTTP ${res.status}`)
  return res.json()
}

/**
 * 流式对话。
 * @param {object} payload        { message, session_id, resume }
 * @param {object} handlers       回调集合
 * @param {function} handlers.onMeta        ({session_id, is_new_session, title}) => void
 * @param {function} handlers.onMemoryStart () => void  —— 开始检索记忆/画像
 * @param {function} handlers.onMemory      (payload) => void  —— 记忆+画像召回明细
 * @param {function} handlers.onDelta       ({text}) => void
 * @param {function} handlers.onToolStart   ({name}) => void
 * @param {function} handlers.onToolResult  ({name, state}) => void
 * @param {function} handlers.onDone        (payload) => void
 * @param {function} handlers.onError       (message) => void
 * @param {AbortSignal} [signal]  用于中断请求
 */
export async function chatStream(payload, handlers = {}, signal) {
  const res = await fetch(`${BASE}/api/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })

  if (!res.ok) {
    // 非 2xx 时后端可能返回 JSON 错误体
    let detail = `HTTP ${res.status}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* 忽略解析失败 */
    }
    handlers.onError?.(detail)
    return
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  // SSE 报文以空行分隔，每条以 "data: {...}" 或 "event: xxx" 开头
  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const frames = buffer.split('\n\n')
    buffer = frames.pop() ?? '' // 末尾可能是不完整帧，留到下次

    for (const frame of frames) {
      const lines = frame.split('\n')
      const eventName = lines
        .find((l) => l.startsWith('event:'))
        ?.slice(6)
        .trim()
      if (eventName === 'end') return

      const dataLine = lines.find((l) => l.startsWith('data:'))
      if (!dataLine) continue

      const raw = dataLine.slice(5).trim()
      if (!raw || raw === '{}') continue

      let event
      try {
        event = JSON.parse(raw)
      } catch {
        continue // 忽略无法解析的心跳/残帧
      }

      switch (event.type) {
        case 'meta':
          handlers.onMeta?.(event)
          break
        case 'memory_start':
          handlers.onMemoryStart?.(event)
          break
        case 'memory':
          handlers.onMemory?.(event)
          break
        case 'delta':
          handlers.onDelta?.(event)
          break
        case 'tool_start':
          handlers.onToolStart?.(event)
          break
        case 'tool_result':
          handlers.onToolResult?.(event)
          break
        case 'done':
          handlers.onDone?.(event)
          break
        case 'error':
          handlers.onError?.(event.message || '未知错误')
          break
        default:
          break
      }
    }
  }
}

/** 非流式对话（降级方案） */
export async function chat(payload) {
  const res = await fetch(`${BASE}/api/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  const body = await res.json().catch(() => null)
  if (!res.ok) {
    throw new Error(body?.detail || `对话失败：HTTP ${res.status}`)
  }
  return body
}
