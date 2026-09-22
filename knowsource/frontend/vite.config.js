import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发态经代理访问后端，避免 CORS；生产由 Nginx 等反代 /api
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8100',
        changeOrigin: true,
      },
    },
  },
})
