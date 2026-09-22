<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import client from '../api/client'

const tenants = ref([])
const currentTenant = ref('')
const statusFilter = ref('pending')
const cases = ref([])
const loading = ref(false)

const confirmVisible = ref(false)
const confirming = ref(false)
const confirmForm = reactive({ caseId: '', originalQuery: '', reviewResult: '', category: '' })

const STATUS_MAP = {
  pending: { label: '待确认', tag: 'warning' },
  confirmed: { label: '已入库', tag: 'success' },
  dismissed: { label: '已驳回', tag: 'info' },
}

async function loadTenants() {
  tenants.value = await client.get('/tenants')
  const active = tenants.value.filter((t) => t.status === 'active' && t.tenant_id !== 'platform')
  if (active.length && !currentTenant.value) {
    currentTenant.value = active[0].tenant_id
    await loadCases()
  }
}

async function loadCases() {
  if (!currentTenant.value) return
  loading.value = true
  try {
    cases.value = await client.get('/cases', {
      params: { tenant_id: currentTenant.value, ...(statusFilter.value ? { status: statusFilter.value } : {}) },
    })
  } finally {
    loading.value = false
  }
}

function openConfirm(row) {
  confirmForm.caseId = row.case_id
  confirmForm.originalQuery = row.original_query
  confirmForm.reviewResult = row.review_result
  confirmForm.category = row.category
  confirmVisible.value = true
}

async function submitConfirm() {
  confirming.value = true
  try {
    await client.post(`/cases/${confirmForm.caseId}/confirm`, {
      review_result: confirmForm.reviewResult,
      category: confirmForm.category,
    })
    ElMessage.success('案例已确认入库，下次类似问题将优先命中')
    confirmVisible.value = false
    await loadCases()
  } finally {
    confirming.value = false
  }
}

async function dismiss(row) {
  await ElMessageBox.confirm('驳回后该审核结论不会进入案例知识库，确认驳回？', '驳回确认', { type: 'warning' })
  await client.post(`/cases/${row.case_id}/dismiss`)
  ElMessage.info('已驳回')
  await loadCases()
}

onMounted(loadTenants)
</script>

<template>
  <div>
    <div class="toolbar">
      <span class="label">租户</span>
      <el-select v-model="currentTenant" style="width: 240px" @change="loadCases">
        <el-option v-for="t in tenants" :key="t.tenant_id" :value="t.tenant_id" :label="`${t.name}（${t.tenant_id}）`" :disabled="t.status !== 'active' || t.tenant_id === 'platform'" />
      </el-select>
      <el-radio-group v-model="statusFilter" @change="loadCases">
        <el-radio-button value="pending">待确认</el-radio-button>
        <el-radio-button value="confirmed">已入库</el-radio-button>
        <el-radio-button value="dismissed">已驳回</el-radio-button>
      </el-radio-group>
    </div>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="flow-tip"
      title="半自动链路：客舟转人工 → 审核结论回写（pending）→ 管理员在此确认/修订（confirmed）→ 案例可被检索优先命中，命中即累计 hit_count"
    />

    <el-table :data="cases" v-loading="loading" stripe>
      <el-table-column prop="case_id" label="案例ID" width="170">
        <template #default="{ row }"><span class="mono">{{ row.case_id }}</span></template>
      </el-table-column>
      <el-table-column prop="original_query" label="触发问题（客户）" min-width="220" show-overflow-tooltip />
      <el-table-column prop="review_result" label="审核结论" min-width="220" show-overflow-tooltip />
      <el-table-column prop="category" label="品类" width="90" />
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="STATUS_MAP[row.status]?.tag" size="small">{{ STATUS_MAP[row.status]?.label || row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="hit_count" label="命中次数" width="90" />
      <el-table-column label="query_id（链路）" width="150">
        <template #default="{ row }"><span class="mono">{{ row.query_id || '—' }}</span></template>
      </el-table-column>
      <el-table-column label="操作" width="150">
        <template #default="{ row }">
          <template v-if="row.status === 'pending'">
            <el-button link type="primary" @click="openConfirm(row)">确认入库</el-button>
            <el-button link type="danger" @click="dismiss(row)">驳回</el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="confirmVisible" title="确认案例入库（可修订审核结论）" width="560px">
      <el-form label-position="top">
        <el-form-item label="触发问题（不可修改）">
          <el-input :model-value="confirmForm.originalQuery" disabled type="textarea" :rows="2" />
        </el-form-item>
        <el-form-item label="审核结论（确认前可修订）">
          <el-input v-model="confirmForm.reviewResult" type="textarea" :rows="4" />
        </el-form-item>
        <el-form-item label="品类">
          <el-input v-model="confirmForm.category" placeholder="如：箱包（可选）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button type="primary" :loading="confirming" @click="submitConfirm">确认并入库</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}
.label {
  font-size: 13px;
  color: #606266;
}
.flow-tip {
  margin-bottom: 16px;
}
.mono {
  font-family: Consolas, monospace;
  font-size: 12px;
}
</style>
