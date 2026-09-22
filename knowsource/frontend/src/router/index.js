import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/knowledge' },
  {
    path: '/knowledge',
    name: 'knowledge',
    component: () => import('../views/KnowledgeManage.vue'),
    meta: { title: '知识管理' },
  },
  {
    path: '/cases',
    name: 'cases',
    component: () => import('../views/CaseInbox.vue'),
    meta: { title: '案例沉淀' },
  },
  {
    path: '/playground',
    name: 'playground',
    component: () => import('../views/RetrievePlayground.vue'),
    meta: { title: '检索测试' },
  },
]

export default createRouter({ history: createWebHistory(), routes })
