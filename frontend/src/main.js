import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

// highlight.js 暗色代码块主题（与方案3 深色代码块背景匹配）
import 'highlight.js/styles/github-dark.css'

import App from './App.vue'
import './styles/variables.scss'

const app = createApp(App)
app.use(ElementPlus)
app.mount('#app')
