import test from 'node:test'
import assert from 'node:assert/strict'
import { request, ApiError } from '../src/api/client.js'

test('reads stay on the local API and carry the session cookie', async () => {
  const original = global.fetch
  global.fetch = async (url, options) => {
    assert.equal(url, '/api/overview')
    assert.equal(options.credentials, 'same-origin')
    assert.equal(options.method, 'GET')
    assert.equal(options.body, undefined)
    return { ok: true, json: async () => ({ cashFlow: { net: 900 } }) }
  }
  try { assert.deepEqual(await request('/overview'), { cashFlow: { net: 900 } }) }
  finally { global.fetch = original }
})
test('mutations send the backend scope and explicit action unchanged', async () => {
  const original = global.fetch
  global.fetch = async (url, options) => {
    assert.equal(url, '/api/budgets')
    assert.equal(options.headers['X-Context-Scope'], 'scope-1')
    assert.deepEqual(JSON.parse(options.body), { monthly_limit: 250, expected_revision: 3, confirmed: true })
    return { ok: true, json: async () => ({ revision: 4 }) }
  }
  try { assert.equal((await request('/budgets', { method: 'PUT', scope: 'scope-1', body: { monthly_limit: 250, expected_revision: 3, confirmed: true } })).revision, 4) }
  finally { global.fetch = original }
})
test('stale proposals surface the backend rejection', async () => {
  const original = global.fetch
  global.fetch = async () => ({ ok: false, status: 409, json: async () => ({ error: { message: 'Proposal no longer matches current state' } }) })
  try { await assert.rejects(request('/proposals/p/decision'), e => e instanceof ApiError && e.status === 409 && e.message.includes('no longer')) }
  finally { global.fetch = original }
})
test('unavailable service errors stay readable', async () => {
  const original = global.fetch
  global.fetch = async () => ({ ok: false, status: 503, json: async () => { throw new Error('not JSON') } })
  try { await assert.rejects(request('/overview'), e => e.status === 503 && e.message.includes('unavailable')) }
  finally { global.fetch = original }
})

test('failed goal admission preserves structured authoritative findings', async () => {
  const original = global.fetch
  const assessment = { status: 'REVISE', schedule: { contribution: 900 }, safety_assessment: { findings: [{ code: 'EXCEEDS_STRETCH_CAPACITY' }] } }
  global.fetch = async () => ({ ok: false, status: 422, json: async () => ({ error: { message: 'Goal not approved', assessment } }) })
  try { await assert.rejects(request('/goals'), error => error.status === 422 && error.assessment === assessment) }
  finally { global.fetch = original }
})
