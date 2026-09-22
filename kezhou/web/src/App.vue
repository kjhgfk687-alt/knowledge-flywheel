<script setup>
import { ref } from 'vue'
import ChatView from './views/ChatView.vue'
import WorkbenchView from './views/WorkbenchView.vue'

const active = ref('chat')
</script>

<template>
  <el-container class="shell">
    <el-header class="shell-header" height="52px">
      <div class="brand">
        客舟 KeZhou
        <span class="sub">电商客服智能体 · 知识飞轮消费端</span>
      </div>
      <el-radio-group v-model="active">
        <el-radio-button value="chat">客户对话</el-radio-button>
        <el-radio-button value="workbench">管理工作台</el-radio-button>
      </el-radio-group>
    </el-header>
    <el-main class="shell-main">
      <KeepAlive>
        <ChatView v-if="active === 'chat'" />
        <WorkbenchView v-else />
      </KeepAlive>
    </el-main>
  </el-container>
</template>

<style scoped>
.shell {
  height: 100vh;
  background: #f0f2f5;
}
.shell-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #e4e7ed;
}
.brand {
  font-weight: 700;
  font-size: 16px;
  color: #303133;
}
.brand .sub {
  font-weight: 400;
  font-size: 12px;
  color: #909399;
  margin-left: 10px;
}
.shell-main {
  padding: 14px;
  max-width: 1100px;
  margin: 0 auto;
  width: 100%;
}
.shell-main > :deep(.el-overlay-parent) {
  height: 100%;
}
.shell-main :deep(.el-container) {
  height: 100%;
}
</style>
