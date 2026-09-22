// 客舟后端 API 封装：dev 走 vite 代理（/api、/ws → 8200），生产同源
async function post(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      detail = (await res.json()).detail || detail
    } catch { /* 保持默认 */ }
    throw new Error(detail)
  }
  return res.json()
}

async function getJson(url) {
  const res = await fetch(url)
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      detail = (await res.json()).detail || detail
    } catch { /* 保持默认 */ }
    throw new Error(detail)
  }
  return res.json()
}

export function sendChat({ tenantId, userId, sessionId, message }) {
  return post('/api/v1/chat', {
    tenant_id: tenantId,
    user_id: userId,
    session_id: sessionId,
    message,
  })
}

export function fetchMetrics(tenantId) {
  return getJson(`/api/v1/metrics?tenant_id=${encodeURIComponent(tenantId)}`)
}

export function fetchApprovals(status = 'pending') {
  return getJson(`/api/v1/approvals?status=${encodeURIComponent(status)}`)
}

export function reviewApproval(approvalId, action, note = '') {
  return post(`/api/v1/approvals/${encodeURIComponent(approvalId)}/review`, { action, note })
}

export function fetchHandoffs() {
  return getJson('/api/v1/handoffs')
}

export function reviewHandoff(handoffId, reviewResult, category = '') {
  return post(`/api/v1/handoffs/${encodeURIComponent(handoffId)}/review`, { review_result: reviewResult, category })
}

export function retryWriteback(handoffId) {
  return post(`/api/v1/handoffs/${encodeURIComponent(handoffId)}/writeback`)
}
