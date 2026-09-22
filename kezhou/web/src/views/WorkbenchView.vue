<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElNotification } from 'element-plus'
import {
  fetchApprovals,
  fetchHandoffs,
  fetchMetrics,
  reviewApproval,
  reviewHandoff,
  retryWriteback,
} from '../api'

const TENANT_ID = 't_bagshop_001'

const metrics = ref(null)
const approvals = ref([])
const handoffs = ref([])
const activeTab = ref('approvals')
const loading = ref(false)

const reviewDialog = reactive({ visible: false, record: null, review_result: '', category: '', submitting: false })
const rejectDialog = reactive({ visible: false, record: null, note: '', submitting: false })

const pendingApprovals = computed(() => approvals.value.length)
const pendingReviews = computed(() => handoffs.value.filter((h) => h.status === 'pending').length)
const cards = computed(() => {
  const m = metrics.value || {}
  const hs = m.handoff_status || {}
  const ws = m.writeback_status || {}
  const as = m.approval_status || {}
  return [
    { label: '累计对话轮次', value: m.turns ?? '-', hint: '全部用户消息数' },
    { label: '转人工率', value: m.transfer_rate != null ? `${(m.transfer_rate * 100).toFixed(1)}%` : '-', hint: `转人工 ${m.transfers ?? 0} 次 / ${m.turns ?? 0} 轮` },
    { label: '待审批', value: as.pending ?? 0, hint: '高风险操作审批单' },
    { label: '待审核转人工', value: hs.pending ?? 0, hint: `已审核 ${hs.reviewed ?? 0}` },
    { label: '回写成功案例', value: ws.success ?? 0, hint: ws.failed ? `失败 ${ws.failed} 待重试` : '飞轮沉淀' },
  ]
})

function reasonTag(reason) {
  if (!reason) return { text: '-', type: 'info' }
  if (reason.includes('retrieval_empty')) return { text: reason, type: 'warning' }
  if (reason.includes('low_confidence')) return { text: reason, type: 'warning' }
  if (reason.includes('emotion')) return { text: reason, type: 'danger' }
  if (reason.includes('risk')) return { text: reason, type: 'danger' }
  return { text: reason, type: 'info' }
}

function writebackTag(row) {
  if (row.status !== 'reviewed') return { text: '未审核', type: 'info' }
  if (row.writeback_status === 'success') return { text: `已回写 ${row.writeback_case_id}`, type: 'success' }
  if (row.writeback_status === 'failed') return { text: '回写失败', type: 'danger' }
  if (row.writeback_status === 'skipped') return { text: '非候选未回写', type: 'info' }
  return { text: row.writeback_status || '-', type: 'info' }
}

function fmtTime(t) {
  return t ? String(t).replace('T', ' ').slice(5, 16) : '-'
}

async function refresh(silent = true) {
  if (!silent) loading.value = true
  try {
    const [m, a, h] = await Promise.all([
      fetchMetrics(TENANT_ID),
      fetchApprovals('pending'),
      fetchHandoffs(),
    ])
    metrics.value = m
    approvals.value = a.approvals
    handoffs.value = h.handoffs
  } catch (e) {
    if (!silent) ElMessage.error(`加载失败：${e.message}`)
  } finally {
    loading.value = false
  }
}

async function onApprove(row) {
  try {
    await reviewApproval(row.id, 'approve', '同意')
    ElMessage.success(`审批单 ${row.id} 已批准`)
    await refresh()
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  }
}

function openReject(row) {
  rejectDialog.record = row
  rejectDialog.note = ''
  rejectDialog.visible = true
}

function openReview(row) {
  reviewDialog.record = row
  reviewDialog.review_result = ''
  reviewDialog.category = ''
  reviewDialog.visible = true
}

async function submitReject() {
  rejectDialog.submitting = true
  try {
    await reviewApproval(rejectDialog.record.id, 'reject', rejectDialog.note)
    ElMessage.success(`审批单 ${rejectDialog.record.id} 已驳回`)
    rejectDialog.visible = false
    await refresh()
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  } finally {
    rejectDialog.submitting = false
  }
}

async function submitReview() {
  if (!reviewDialog.review_result.trim()) {
    ElMessage.warning('请填写审核结论（将作为审核理由回写知源案例库）')
    return
  }
  reviewDialog.submitting = true
  try {
    const res = await reviewHandoff(reviewDialog.record.id, reviewDialog.review_result, reviewDialog.category)
    if (res.writeback.status === 'success') {
      ElMessage.success(`审核完成，已回写知源（案例 ${res.writeback.case_id}，待知源确认入库）`)
    } else if (res.writeback.status === 'skipped') {
      ElMessage.info('审核完成：非回写候选原因，不沉淀为案例')
    } else {
      ElMessage.warning('审核完成，但回写失败，可在列表中重试')
    }
    reviewDialog.visible = false
    await refresh()
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  } finally {
    reviewDialog.submitting = false
  }
}

async function onRetryWriteback(row) {
  try {
    const res = await retryWriteback(row.id)
    if (res.status === 'success') ElMessage.success(`回写成功：${res.case_id}`)
    else ElMessage.warning(`回写仍失败：${res.error_code}`)
    await refresh()
  } catch (e) {
    ElMessage.error(`操作失败：${e.message}`)
  }
}

// 坐席 WebSocket：转人工实时通知 + 断线重连；轮询兜底
let ws = null
let reconnectTimer = null
let pollTimer = null

function connectWs() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  ws = new WebSocket(`${proto}://${location.host}/ws/agent`)
  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data)
      if (msg.type === 'handoff') {
        ElNotification({
          title: '新转人工会话',
          message: `原因 ${msg.record.transfer_reason}：${String(msg.record.query).slice(0, 40)}`,
          type: 'warning',
        })
        refresh()
      }
    } catch { /* 忽略非 JSON 帧 */ }
  }
  ws.onclose = () => {
    reconnectTimer = setTimeout(connectWs, 5000)
  }
}

onMounted(() => {
  refresh(false)
  connectWs()
  pollTimer = setInterval(() => refresh(), 15000)
})

onBeforeUnmount(() => {
  clearTimeout(reconnectTimer)
  clearInterval(pollTimer)
  if (ws) ws.onclose = null
  if (ws && ws.readyState <= 1) ws.close()
})
</script>

<template>
  <div v-loading="loading" class="wb">
    <!-- 指标卡 -->
    <el-row :gutter="12" class="cards">
      <el-col v-for="card in cards" :key="card.label" :span="4" :xs="12">
        <el-card shadow="never" class="stat-card">
          <div class="stat-value">{{ card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
          <div class="stat-hint">{{ card.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-tabs v-model="activeTab" class="wb-tabs">
      <!-- 待审批队列 -->
      <el-tab-pane name="approvals">
        <template #label>
          待审批
          <el-badge v-if="pendingApprovals" :value="pendingApprovals" type="danger" class="tab-badge" />
        </template>
        <el-table :data="approvals" size="default" empty-text="暂无待审批请求（高风险操作会出现在这里）">
          <el-table-column prop="id" label="审批单号" width="150" />
          <el-table-column label="时间" width="120">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column prop="action_type" label="类型" width="90" />
          <el-table-column prop="order_id" label="订单" width="110" />
          <el-table-column label="金额" width="110">
            <template #default="{ row }">¥{{ row.amount != null ? row.amount.toFixed(2) : '-' }}</template>
          </el-table-column>
          <el-table-column label="风控因子" min-width="220">
            <template #default="{ row }">
              <el-tag v-for="f in row.risk_factors" :key="f" size="small" type="warning" class="factor">{{ f }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button type="success" size="small" @click="onApprove(row)">批准</el-button>
              <el-button type="danger" size="small" plain @click="openReject(row)">
                驳回
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 转人工审核 -->
      <el-tab-pane name="handoffs">
        <template #label>
          转人工审核
          <el-badge v-if="pendingReviews" :value="pendingReviews" type="warning" class="tab-badge" />
        </template>
        <el-table :data="handoffs" empty-text="暂无转人工记录">
          <el-table-column prop="id" label="记录号" width="140" />
          <el-table-column label="时间" width="120">
            <template #default="{ row }">{{ fmtTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="原因" width="200">
            <template #default="{ row }">
              <el-tag size="small" :type="reasonTag(row.transfer_reason).type">{{ row.transfer_reason }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="query" label="用户问题" min-width="200" show-overflow-tooltip />
          <el-table-column prop="query_id" label="query_id" width="130">
            <template #default="{ row }">
              <span class="mono">{{ row.query_id || '-' }}</span>
            </template>
          </el-table-column>
          <el-table-column label="回写状态" width="170">
            <template #default="{ row }">
              <el-tag size="small" :type="writebackTag(row).type">{{ writebackTag(row).text }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="150" fixed="right">
            <template #default="{ row }">
              <el-button
                type="primary"
                size="small"
                :disabled="row.status !== 'pending'"
                @click="openReview(row)"
              >
                审核
              </el-button>
              <el-button
                v-if="row.writeback_status === 'failed'"
                size="small"
                type="warning"
                plain
                @click="onRetryWriteback(row)"
              >
                重试回写
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-tab-pane>

      <!-- 案例库（知源侧，飞轮沉淀结果） -->
      <el-tab-pane name="cases" label="案例库（知源）">
        <el-alert
          v-if="metrics && metrics.zhiyuan_cases_ok === false"
          type="error"
          :title="`知源案例库不可达：${metrics.zhiyuan_cases_error}`"
          :closable="false"
          class="case-alert"
        />
        <el-table :data="metrics ? metrics.zhiyuan_cases : []" empty-text="暂无案例（转人工审核回写后，经知源确认入库即出现在这里）">
          <el-table-column prop="case_id" label="案例 ID" width="170" />
          <el-table-column prop="original_query" label="用户问题" min-width="180" show-overflow-tooltip />
          <el-table-column prop="review_result" label="审核结论" min-width="220" show-overflow-tooltip />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'confirmed' ? 'success' : row.status === 'pending' ? 'warning' : 'info'">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="hit_count" label="命中次数" width="100" sortable />
          <el-table-column prop="feedback_source" label="来源" width="90" />
        </el-table>
      </el-tab-pane>
    </el-tabs>

    <!-- 审核弹窗 -->
    <el-dialog v-model="reviewDialog.visible" title="转人工审核（结论将回写知源案例库）" width="520">
      <div v-if="reviewDialog.record" class="dialog-meta">
        <el-tag size="small" :type="reasonTag(reviewDialog.record.transfer_reason).type">
          {{ reviewDialog.record.transfer_reason }}
        </el-tag>
        <span class="dialog-query">{{ reviewDialog.record.query }}</span>
        <div class="mono small">query_id：{{ reviewDialog.record.query_id || '-' }}</div>
      </div>
      <el-input
        v-model="reviewDialog.review_result"
        type="textarea"
        :rows="4"
        maxlength="2000"
        show-word-limit
        placeholder="填写人工审核结论（即回写知源的审核理由，例如：经核实为真皮，已向客户解释材质工艺）"
      />
      <el-input v-model="reviewDialog.category" class="dialog-category" maxlength="64" placeholder="商品品类（可选，如：箱包）" />
      <template #footer>
        <el-button @click="reviewDialog.visible = false">取消</el-button>
        <el-button type="primary" :loading="reviewDialog.submitting" @click="submitReview">提交并回写</el-button>
      </template>
    </el-dialog>

    <!-- 驳回弹窗 -->
    <el-dialog v-model="rejectDialog.visible" title="驳回审批" width="460">
      <div class="dialog-meta">
        审批单 <span class="mono">{{ rejectDialog.record && rejectDialog.record.id }}</span>
      </div>
      <el-input v-model="rejectDialog.note" type="textarea" :rows="3" maxlength="2000" placeholder="驳回原因（可选）" />
      <template #footer>
        <el-button @click="rejectDialog.visible = false">取消</el-button>
        <el-button type="danger" :loading="rejectDialog.submitting" @click="submitReject">确认驳回</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.wb {
  padding: 4px 2px;
}
.cards {
  margin-bottom: 14px;
}
.stat-card {
  text-align: center;
}
.stat-value {
  font-size: 26px;
  font-weight: 700;
  color: #303133;
}
.stat-label {
  font-size: 13px;
  color: #606266;
  margin-top: 2px;
}
.stat-hint {
  font-size: 11px;
  color: #909399;
  margin-top: 2px;
}
.tab-badge {
  margin-left: 6px;
  vertical-align: middle;
}
.factor {
  margin: 2px 4px 2px 0;
}
.mono {
  font-family: monospace;
  font-size: 12px;
}
.small {
  color: #909399;
}
.dialog-meta {
  margin-bottom: 10px;
  line-height: 1.8;
}
.dialog-query {
  margin-left: 8px;
  font-weight: 600;
}
.dialog-category {
  margin-top: 10px;
}
.case-alert {
  margin-bottom: 12px;
}
</style>
