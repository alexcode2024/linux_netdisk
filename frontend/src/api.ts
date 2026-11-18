export const api = async <Result,>(path: string, init?: RequestInit): Promise<Result> => {
  const response = await fetch(path, { credentials: 'same-origin', ...init })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: '请求失败' }))
    if (typeof error.detail === 'string') throw new Error(error.detail)
    const fields: Record<string, string> = { file_id: '文件标识', url_type: '分享方式', expired_type: '有效期' }
    const messages: Record<string, string> = { string_pattern_mismatch: '格式不正确', literal_error: '值不在允许范围内', missing: '不能为空', string_too_long: '内容过长' }
    const details = Array.isArray(error.detail) ? error.detail.flatMap((detail: { msg?: string; loc?: unknown[]; type?: string } | null) => {
      if (!detail || typeof detail.msg !== 'string') return []
      const path = Array.isArray(detail.loc) ? detail.loc.filter((part: unknown) => part !== 'body').join('.') : ''
      return [`${fields[path] || path || '参数'}：${messages[detail.type || ''] || detail.msg}`]
    }) : []
    throw new Error(details.join('；') || '请求失败，请稍后重试')
  }
  return response.json()
}
