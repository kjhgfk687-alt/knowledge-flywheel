<script setup>
import { useRoute } from 'vue-router'

const route = useRoute()
</script>

<template>
  <el-container style="min-height: 100vh">
    <el-aside width="220px" class="aside">
      <div class="brand">
        <div class="brand-name">知源 KnowSource</div>
        <div class="brand-sub">商户知识运营平台</div>
      </div>
      <el-menu :default-active="route.path" router>
        <el-menu-item index="/knowledge">知识管理</el-menu-item>
        <el-menu-item index="/cases">案例沉淀</el-menu-item>
        <el-menu-item index="/playground">检索测试</el-menu-item>
      </el-menu>
    </el-aside>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style scoped>
.aside {
  border-right: 1px solid #e4e7ed;
}
.brand {
  padding: 20px 16px;
}
.brand-name {
  font-size: 17px;
  font-weight: 600;
}
.brand-sub {
  margin-top: 4px;
  font-size: 12px;
  color: #909399;
}
.main {
  background: #f6f8fa;
}
</style>
