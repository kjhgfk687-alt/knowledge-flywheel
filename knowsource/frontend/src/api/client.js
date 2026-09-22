import axios from 'axios'
import { ElMessage } from 'element-plus'

/**
 * 控制台 API 客户端。
 * 统一解析后端契约信封 {code, message, data}：code!==0 视为错误。
 */
const client = axios.create({ baseURL: '/api/v1', timeout: 60000 })

// 服务间共享密钥（契约 §1）：与后端 .env 的 KS_API_KEY 同值；构建时可用 VITE_API_KEY 覆盖
client.defaults.headers.common['X-API-Key'] = import.meta.env.VITE_API_KEY || 'zs-kz-dev-key-001'

client.interceptors.response.use(
  (resp) => {
    const body = resp.data
    if (body && typeof body === 'object' && 'code' in body) {
      if (body.code !== 0) {
        ElMessage.error(body.message || `请求失败（${body.code}）`)
        return Promise.reject(new Error(body.message))
      }
      return body.data
    }
    return body
  },
  (err) => {
    const body = err.response?.data
    const msg = body?.message || err.message || '网络错误'
    ElMessage.error(`${body?.code ? `错误 ${body.code}：` : ''}${msg}`)
    return Promise.reject(err)
  },
)

export default client
