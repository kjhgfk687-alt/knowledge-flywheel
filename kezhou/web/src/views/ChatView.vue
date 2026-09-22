<script setup>
import { nextTick, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { sendChat } from '../api'

// 演示固定租户；正式版由渠道/组件配置注入，绝不取自终端用户输入
const TENANT_ID = 't_bagshop_001'
const SOURCE_LABELS = { platform: '平台政策', merchant: '商户文档', case: '历史审核案例' }

const input = ref('')
const loading = ref(false)
const sessionId = ref(null)
const listEl = ref(null)
const messages = reactive([
  { role: 'assistant', text: '您好，我是客舟智能客服，请问有什么可以帮您？', citations: [] },
])

// 把回答文本按 [n] 切段：数字段渲染为可点上标，命中 citations 才可弹卡
function segments(text, citations) {
  return String(text)
    .split(/(\[\d+\])/g)
    .filter(Boolean)
    .map((part) => {
      const m = part.match(/^\[(\d+)\]$/)
      if (!m) return { type: 'text', text: part }
      const n = Number(m[1])
      return { type: 'cite', n, citation: citations.find((c) => c.n === n) || null }
    })
}

async function onSend() {
  const text = input.value.trim()
  if (!text || loading.value) return
  input.value = ''
  messages.push({ role: 'user', text, citations: [] })
  loading.value = true
  try {
    const res = await sendChat({
      tenantId: TENANT_ID,
      userId: 'u_demo_001',
      sessionId: sessionId.value,
      message: text,
    })
    sessionId.value = res.session_id
    messages.push({
      role: 'assistant',
      text: res.reply_text,
      citations: res.citations || [],
      needHuman: res.need_human,
      unverified: res.unverified,
      degraded: res.retrieval_degraded,
      transferReason: res.transfer_reason,
      queryId: res.query_id,
      riskLevel: res.risk_level,
      riskFactors: res.risk_factors || [],
    })
  } catch (e) {
    ElMessage.error(`请求失败：${e.message}（请确认客舟服务已启动）`)
  } finally {
    loading.value = false
  }
}

watch(() => messages.length, async () => {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
})
</script>

<template>
  <el-container class="chat-page">
    <el-header class="chat-header" height="48px">
      <div class="title">智能客服对话窗</div>
      <div class="meta">
        <el-tag size="small" type="info" effect="plain">租户 {{ TENANT_ID }}</el-tag>
        <el-tag v-if="sessionId" size="small" type="info" effect="plain">会话 {{ sessionId }}</el-tag>
      </div>
    </el-header>

    <el-main ref="listEl" class="chat-list">
      <div v-for="(msg, i) in messages" :key="i" class="row" :class="msg.role">
        <div class="bubble" :class="msg.role">
          <template v-if="msg.role === 'user'">{{ msg.text }}</template>
          <template v-else>
            <span v-for="(seg, j) in segments(msg.text, msg.citations)" :key="j">
              <template v-if="seg.type === 'text'">{{ seg.text }}</template>
              <template v-else-if="seg.citation">
                <el-popover placement="top" :width="300" trigger="click">
                  <template #reference>
                    <sup class="cite">[{{ seg.n }}]</sup>
                  </template>
                  <div class="cite-card">
                    <div class="cite-title">
                      {{ seg.citation.source_title }}
                    </div>
                    <div class="cite-tags">
                      <el-tag size="small">{{ SOURCE_LABELS[seg.citation.source_type] || seg.citation.source_type }}</el-tag>
                      <el-tag v-if="seg.citation.from_case" size="small" type="warning" effect="dark">
                        来自历史审核案例
                      </el-tag>
                    </div>
                    <div class="cite-chunk">chunk_id：{{ seg.citation.chunk_id }}</div>
                  </div>
                </el-popover>
              </template>
              <template v-else><sup class="cite missing">[{{ seg.n }}]</sup></template>
            </span>
            <div v-if="msg.needHuman || msg.unverified || msg.degraded || (msg.riskFactors && msg.riskFactors.length)" class="badges">
              <el-tag v-if="msg.needHuman" size="small" type="danger">已转人工（{{ msg.transferReason }}）</el-tag>
              <el-tag v-if="msg.unverified" size="small" type="warning">未经验证的回答</el-tag>
              <el-tag v-if="msg.degraded" size="small" type="warning">知识库降级检索</el-tag>
              <el-tag
                v-if="msg.riskFactors && msg.riskFactors.length"
                size="small"
                :type="msg.riskLevel === 'high' ? 'danger' : msg.riskLevel === 'medium' ? 'warning' : 'success'"
              >
                风控：{{ msg.riskFactors.join('；') }}
              </el-tag>
            </div>
          </template>
        </div>
      </div>
      <div v-if="loading" class="row assistant">
        <div class="bubble assistant loading-dot">正在查询知识库…</div>
      </div>
    </el-main>

    <!-- 回车发送挂在自有容器上靠原生冒泡触发：该版本 el-input 不转发键盘监听 -->
    <el-footer class="chat-input" height="64px" @keyup.enter="onSend">
      <el-input
        v-model="input"
        placeholder="输入您的问题，例如：客户说买的包五金掉色怀疑是假货，怎么处理？"
        size="large"
        :disabled="loading"
      />
      <el-button type="primary" size="large" :loading="loading" @click="onSend">发送</el-button>
    </el-footer>
  </el-container>
</template>

<style scoped>
.chat-page {
  height: 100%;
  background: #f5f7fa;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
}
.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}
.chat-header .title {
  font-weight: 600;
  font-size: 15px;
}
.chat-header .meta {
  display: flex;
  gap: 8px;
}
.chat-list {
  overflow-y: auto;
  padding: 20px;
}
.row {
  display: flex;
  margin-bottom: 14px;
}
.row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 10px;
  line-height: 1.7;
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble.user {
  background: #409eff;
  color: #fff;
  border-top-right-radius: 2px;
}
.bubble.assistant {
  background: #fff;
  border: 1px solid #e4e7ed;
  border-top-left-radius: 2px;
}
.bubble.loading-dot {
  color: #909399;
}
.cite {
  color: #409eff;
  cursor: pointer;
  font-weight: 600;
  margin: 0 2px;
}
.cite.missing {
  color: #f56c6c;
  cursor: default;
}
.cite-card .cite-title {
  font-weight: 600;
  margin-bottom: 8px;
}
.cite-card .cite-tags {
  display: flex;
  gap: 6px;
  margin-bottom: 8px;
}
.cite-card .cite-chunk {
  font-family: monospace;
  font-size: 12px;
  color: #909399;
}
.badges {
  margin-top: 8px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.chat-input {
  display: flex;
  gap: 10px;
  align-items: center;
  background: #fff;
  border-top: 1px solid #e4e7ed;
}
</style>
