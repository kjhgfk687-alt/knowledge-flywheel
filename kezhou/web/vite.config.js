import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// dev 代理：/api、/ws → 客舟 FastAPI(8200)，生产同源部署后删除。
// 端口约定：知源前端 5173 / 客舟前端 5174（对齐后端 8100/8200 的分治）；
// strictPort：被占用时显式报错，不悄悄漂移到其他端口
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      '/api': 'http://localhost:8200',
      '/ws': { target: 'http://localhost:8200', ws: true },
    },
  },
})
