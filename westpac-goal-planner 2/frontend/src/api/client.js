export class ApiError extends Error {
  constructor(message, status, assessment) { super(message); this.status = status; this.assessment = assessment }
}

export async function request(path, { method = 'GET', body, scope, signal, keepalive = false } = {}) {
  const response = await fetch(`/api${path}`, {
    method, credentials: 'same-origin', signal, keepalive,
    headers: { ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(scope ? { 'X-Context-Scope': scope } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const result = await response.json().catch(() => ({}))
  if (!response.ok) throw new ApiError(result.error?.message || 'The banking service is unavailable.', response.status, result.error?.assessment)
  return result
}
