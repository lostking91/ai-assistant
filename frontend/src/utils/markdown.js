// Markdown 渲染 + 代码高亮
//
// 注意：marked v5+ 已移除 `highlight` 配置项，因此这里采用
// 「marked 渲染 -> DOMPurify 消毒 -> 事后用 highlight.js 高亮 DOM」的稳妥方案，
// 不依赖 marked 具体版本的选项，也避免 XSS 风险。

import { marked } from 'marked'
import hljs from 'highlight.js'
import DOMPurify from 'dompurify'

marked.setOptions({
  gfm: true, // 支持表格、删除线等 GitHub 风格语法
  breaks: true, // 单个换行渲染为 <br>
})

/**
 * 把 Markdown 文本渲染为经过消毒的 HTML 字符串。
 * @param {string} text
 * @returns {string}
 */
export function renderMarkdown(text) {
  if (!text) return ''
  const raw = marked.parse(text)
  return DOMPurify.sanitize(raw, {
    ADD_ATTR: ['target', 'rel'],
  })
}

/**
 * 对已渲染到 DOM 的容器内所有代码块执行高亮。
 * 需在 v-html 更新后调用（onMounted / onUpdated）。
 * @param {HTMLElement} el
 */
export function highlightCodeBlocks(el) {
  if (!el) return
  el.querySelectorAll('pre code').forEach((block) => {
    if (block.dataset.highlighted === 'yes') return
    hljs.highlightElement(block)
  })

  // 外部链接新开窗口
  el.querySelectorAll('a[href^="http"]').forEach((a) => {
    a.setAttribute('target', '_blank')
    a.setAttribute('rel', 'noopener noreferrer')
  })
}

/**
 * 流式输出场景下的高亮：为避免频繁全量重算，
 * 只对尚未标记的代码块处理，并对不完整的尾部代码块做容错。
 * @param {HTMLElement} el
 */
export function highlightStreaming(el) {
  if (!el) return
  const blocks = el.querySelectorAll('pre code')
  blocks.forEach((block, index) => {
    const isLast = index === blocks.length - 1
    // 流式过程中最后一个代码块可能还没闭合，重复高亮代价高，跳过已处理的
    if (block.dataset.highlighted === 'yes' && !isLast) return
    try {
      block.removeAttribute('data-highlighted')
      hljs.highlightElement(block)
    } catch {
      // 不完整的代码片段高亮失败时忽略，等下一帧再试
    }
  })
}
