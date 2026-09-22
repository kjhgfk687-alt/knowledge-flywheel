<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import client from '../api/client'

const tenants = ref([])
const currentTenant = ref('')
const layerMode = ref('merchant') // merchant=商户层（租户隔离） | platform=平台层（共享）
const docs = ref([])
const loading = ref(false)

const uploadVisible = ref(false)
const uploading = ref(false)
const uploadForm = reactive({ title: '', category: '', file: null })

const detailVisible = ref(false)
const detail = ref(null)

const STATUS_MAP = {
  indexed: { label: '已索引', tag: 'success' },
  uploaded: { label: '待处理', tag: 'info' },
  parsing: { label: '解析中', tag: 'warning' },
  failed: { label: '失败', tag: 'danger' },
}

async function loadTenants() {
  tenants.value = await client.get('/tenants')
  if (tenants.value.length && !currentTenant.value) {
    currentTenant.value = tenants.value[0].tenant_id
    await loadDocs()
  }
}

async function loadDocs() {
  if (layerMode.value === 'merchant' && !currentTenant.value) return
  loading.value = true
  try {
    const params = { layer: layerMode.value }
    if (layerMode.value === 'merchant') params.tenant_id = currentTenant.value
    docs.value = await client.get('/documents', { params })
  } finally {
    loading.value = false
  }
}

function switchLayer() {
  uploadForm.title = ''
  uploadForm.category = ''
  uploadForm.file = null
  loadDocs()
}

function onFileChange(file) {
  uploadForm.file = file.raw
  if (!uploadForm.title) uploadForm.title = file.name.replace(/\.[^.]+$/, '')
}

async function submitUpload() {
  if (!uploadForm.file) {
    ElMessage.warning('请选择文件（pdf / md / txt）')
    return
  }
  uploading.value = true
  try {
    const fd = new FormData()
    fd.append('layer', layerMode.value)
    if (layerMode.value === 'merchant') {
      fd.append('tenant_id', currentTenant.value)
      fd.append('category', uploadForm.category)
    }
    if (uploadForm.title) fd.append('title', uploadForm.title)
    fd.append('file', uploadForm.file)
    const doc = await client.post('/documents/upload', fd)
    if (doc.status === 'indexed') ElMessage.success(`上传成功，切分为 ${doc.chunk_count} 个切片`)
    else ElMessage.warning(`上传完成但摄取失败：${doc.error_msg || '未知原因'}`)
    uploadVisible.value = false
    uploadForm.title = ''
    uploadForm.category = ''
    uploadForm.file = null
    await loadDocs()
  } finally {
    uploading.value = false
  }
}

async function openDetail(row) {
  const params = { layer: row.layer }
  if (row.layer === 'merchant') params.tenant_id = currentTenant.value
  detail.value = await client.get(`/documents/${row.id}`, { params })
  detailVisible.value = true
}

onMounted(loadTenants)
</script>

<template>
  <div>
    <div class="toolbar">
      <el-radio-group v-model="layerMode" @change="switchLayer">
        <el-radio-button value="merchant">商户层（租户隔离）</el-radio-button>
        <el-radio-button value="platform">平台层（共享政策）</el-radio-button>
      </el-radio-group>
      <template v-if="layerMode === 'merchant'">
        <span class="label">当前租户</span>
        <el-select v-model="currentTenant" style="width: 240px" @change="loadDocs">
          <el-option v-for="t in tenants" :key="t.tenant_id" :value="t.tenant_id" :label="`${t.name}（${t.tenant_id}）`" :disabled="t.status !== 'active'" />
        </el-select>
      </template>
      <el-alert
        v-else
        title="平台层知识全商户共享，检索时作为兜底约束附在证据末尾"
        type="info"
        :closable="false"
        style="flex: 1"
      />
      <el-button type="primary" :icon="Upload" @click="uploadVisible = true">上传文档</el-button>
      <el-button @click="loadDocs">刷新</el-button>
    </div>

    <el-table :data="docs" v-loading="loading" stripe>
      <el-table-column prop="id" label="ID" width="60" />
      <el-table-column prop="title" label="标题" min-width="220" show-overflow-tooltip />
      <el-table-column prop="category" label="品类" width="100" />
      <el-table-column prop="file_type" label="类型" width="80" />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="STATUS_MAP[row.status]?.tag || 'info'" size="small">
            {{ STATUS_MAP[row.status]?.label || row.status }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="chunk_count" label="切片数" width="90" />
      <el-table-column label="上传时间" width="180">
        <template #default="{ row }">{{ (row.created_at || '').replace('T', ' ').slice(0, 19) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="100">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="uploadVisible" title="上传知识文档" width="480px">
      <el-form label-width="80px">
        <el-form-item label="标题">
          <el-input v-model="uploadForm.title" placeholder="默认取文件名" />
        </el-form-item>
        <el-form-item label="品类">
          <el-input v-model="uploadForm.category" placeholder="如：箱包（可选）" />
        </el-form-item>
        <el-form-item label="文件">
          <el-upload drag :auto-upload="false" :limit="1" accept=".pdf,.md,.txt" :on-change="onFileChange">
            <div class="el-upload__text">拖拽文件到此处，或 <em>点击选择</em></div>
            <template #tip>
              <div class="el-upload__tip">支持 pdf / md / txt，10MB 以内</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="uploadVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="submitUpload">上传并索引</el-button>
      </template>
    </el-dialog>

    <el-drawer v-model="detailVisible" :title="detail?.title" size="45%">
      <div v-if="detail" class="detail-meta">
        <el-tag size="small">状态：{{ STATUS_MAP[detail.status]?.label || detail.status }}</el-tag>
        <el-tag size="small" type="info">切片数：{{ detail.chunk_count }}</el-tag>
        <el-tag v-if="detail.error_msg" size="small" type="danger">{{ detail.error_msg }}</el-tag>
      </div>
      <el-card v-for="c in detail?.chunks || []" :key="c.chunk_id" shadow="never" class="chunk-card">
        <div class="chunk-head">#{{ c.chunk_index }} · {{ c.chunk_id }}</div>
        <div class="chunk-body">{{ c.content }}</div>
      </el-card>
    </el-drawer>
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
.detail-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.chunk-card {
  margin-bottom: 10px;
}
.chunk-head {
  font-size: 12px;
  color: #909399;
  margin-bottom: 6px;
}
.chunk-body {
  white-space: pre-wrap;
  font-size: 13px;
  line-height: 1.7;
}
</style>
