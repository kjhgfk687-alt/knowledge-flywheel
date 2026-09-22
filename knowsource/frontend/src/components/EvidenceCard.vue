<script setup>
import { computed } from 'vue'

const props = defineProps({
  evidence: { type: Object, required: true },
})

const SOURCE_MAP = {
  merchant: { label: '商户层', tag: 'success' },
  platform: { label: '平台层', tag: 'primary' },
  case: { label: '案例', tag: 'warning' },
}

const source = computed(() => SOURCE_MAP[props.evidence.source_type] || { label: props.evidence.source_type, tag: 'info' })
const scorePercent = computed(() => Math.round(props.evidence.score * 1000) / 10)
const meta = computed(() => props.evidence.metadata || {})
const caseFields = computed(() =>
  ['case_id', 'original_query', 'review_result', 'hit_count', 'feedback_source'].filter((k) => meta.value[k] !== undefined),
)
</script>

<template>
  <el-card shadow="hover" class="evidence-card">
    <template #header>
      <div class="card-header">
        <div class="header-left">
          <el-tag :type="source.tag" effect="dark" size="small">{{ source.label }}</el-tag>
          <span class="title">{{ evidence.source_title }}</span>
        </div>
        <el-tag size="small" type="info" class="chunk-id">{{ evidence.chunk_id }}</el-tag>
      </div>
    </template>

    <div class="content">{{ evidence.content }}</div>

    <div class="score-row">
      <span class="score-label">相关度 {{ evidence.score.toFixed(4) }}</span>
      <el-progress :percentage="scorePercent" :stroke-width="8" class="score-bar" />
    </div>

    <div class="meta-row">
      <el-tag v-if="meta.category" size="small" type="info">品类：{{ meta.category }}</el-tag>
      <span class="meta-item">出处：{{ meta.doc_id }} · 切片 #{{ meta.chunk_index }}</span>
      <span class="meta-item">入库：{{ (meta.created_at || '').slice(0, 10) }}</span>
    </div>

    <div v-if="caseFields.length" class="case-box">
      <div v-if="meta.original_query" class="case-line"><b>触发问题：</b>{{ meta.original_query }}</div>
      <div v-if="meta.review_result" class="case-line"><b>审核结论：</b>{{ meta.review_result }}</div>
      <div class="case-line meta-item">
        <el-tag v-if="meta.hit_count !== undefined" size="small" type="warning">命中 {{ meta.hit_count }} 次</el-tag>
        <el-tag v-if="meta.feedback_source" size="small">来源：{{ meta.feedback_source }}</el-tag>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.evidence-card {
  margin-bottom: 14px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.title {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.chunk-id {
  flex-shrink: 0;
}
.content {
  white-space: pre-wrap;
  line-height: 1.7;
  color: #303133;
}
.score-row {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 12px;
}
.score-label {
  font-size: 12px;
  color: #606266;
  white-space: nowrap;
}
.score-bar {
  flex: 1;
}
.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 10px;
  font-size: 12px;
  color: #909399;
}
.case-box {
  margin-top: 12px;
  padding: 10px 12px;
  background: #fdf6ec;
  border-radius: 6px;
  font-size: 13px;
}
.case-line {
  line-height: 1.8;
}
</style>
