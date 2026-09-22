<script setup>
import { onMounted, reactive, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import client from '../api/client'
import EvidenceCard from '../components/EvidenceCard.vue'

const tenants = ref([])
const currentTenant = ref('')
const loading = ref(false)
const result = ref(null)

const form = reactive({
  query: '',
  top_k: 5,
  include_platform: true,
  include_case: true,
  category: '',
  auth_status: [],
})

async function loadTenants() {
  tenants.value = await client.get('/tenants')
  if (tenants.value.length) currentTenant.value = tenants.value[0].tenant_id
}

async function search() {
  if (!form.query.trim()) return
  loading.value = true
  result.value = null
  try {
    const filters = {}
    if (form.category.trim()) filters.category = form.category.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    if (form.auth_status.length) filters.auth_status = form.auth_status
    const data = await client.post('/retrieve', {
      tenant_id: currentTenant.value,
      query: form.query,
      top_k: form.top_k,
      include_platform: form.include_platform,
      include_case: form.include_case,
      ...(Object.keys(filters).length ? { filters } : {}),
    })
    result.value = data
  } finally {
    loading.value = false
  }
}

onMounted(loadTenants)
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="8">
      <el-card shadow="never" class="panel">
        <template #header><b>检索请求</b></template>
        <el-form label-position="top">
          <el-form-item label="租户">
            <el-select v-model="currentTenant" style="width: 100%">
              <el-option v-for="t in tenants" :key="t.tenant_id" :value="t.tenant_id" :label="`${t.name}（${t.tenant_id}）`" :disabled="t.status !== 'active'" />
            </el-select>
          </el-form-item>
          <el-form-item label="查询问题">
            <el-input v-model="form.query" type="textarea" :rows="4" placeholder="模拟客服问题，如：客户说买的包五金掉色怀疑假货要求退款怎么处理" />
          </el-form-item>
          <el-form-item :label="`top_k：${form.top_k}`">
            <el-slider v-model="form.top_k" :min="1" :max="20" show-input :show-input-controls="false" />
          </el-form-item>
          <el-form-item label="品类过滤（逗号分隔，可选）">
            <el-input v-model="form.category" placeholder="如：箱包,手表" />
          </el-form-item>
          <el-form-item label="鉴定状态过滤（可选）">
            <el-select v-model="form.auth_status" multiple style="width: 100%" placeholder="不选=不过滤">
              <el-option label="正品 authentic" value="authentic" />
              <el-option label="假货 fake" value="fake" />
              <el-option label="未知 unknown" value="unknown" />
            </el-select>
          </el-form-item>
          <el-form-item>
            <el-checkbox v-model="form.include_platform">包含平台层</el-checkbox>
            <el-checkbox v-model="form.include_case">包含案例层</el-checkbox>
          </el-form-item>
          <el-button type="primary" :icon="Search" :loading="loading" style="width: 100%" @click="search">
            检 索
          </el-button>
        </el-form>
      </el-card>
    </el-col>

    <el-col :span="16">
      <el-card v-if="result" shadow="never" class="panel">
        <template #header>
          <div class="result-head">
            <b>检索结果</b>
            <span class="meta">
              query_id: {{ result.query_id }} · {{ result.total }} 条 · {{ result.latency_ms }}ms
              <el-tag v-if="result.degraded" type="danger" size="small" effect="dark" style="margin-left: 8px">
                已降级：{{ result.degrade_reason }}
              </el-tag>
            </span>
          </div>
        </template>

        <el-alert
          v-if="result.degraded"
          type="warning"
          :closable="false"
          show-icon
          class="degrade-alert"
          :title="`向量检索不可用（${result.degrade_reason}），以下结果来自关键词降级，置信度请按低分处理`"
        />

        <el-empty v-if="!result.retrieved" description="未找到相关知识（retrieved=false）——应引导补充信息或转人工，而非编造回答" />
        <template v-else>
          <EvidenceCard v-for="e in result.evidence" :key="e.chunk_id" :evidence="e" />
        </template>
      </el-card>
      <el-card v-else shadow="never" class="panel">
        <el-empty description="输入问题并点击检索，查看契约返回的证据卡片" />
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
.panel {
  min-height: 70vh;
}
.result-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.meta {
  font-size: 12px;
  color: #909399;
}
.degrade-alert {
  margin-bottom: 14px;
}
</style>
