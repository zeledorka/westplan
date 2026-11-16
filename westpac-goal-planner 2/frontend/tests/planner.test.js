import test from 'node:test'
import assert from 'node:assert/strict'
import { createPlanner, calculatedOptions } from '../src/api/planner.js'

const draft = { draft_id: 'd', session_id: 'conversation', status: 'draft_unverified', activation_allowed: false,
  result: { recommended_ref: 'calculation-2', strategies: [{ calculation_ref: 'calculation-1', title: 'A composed option' }, { calculation_ref: 'calculation-2', title: 'Another approach' }] },
  calculations: { 'calculation-1': { plans: [{ contribution: 311.17 }] }, 'calculation-2': { plans: [{ contribution: 193.42 }] } } }
function setup(handler) {
  const calls = []; let requestNumber = 0
  const api = async (path, options = {}) => {
    calls.push([path, options])
    if (handler) { const result = await handler(path, options); if (result !== undefined) return result }
    if (path === '/planner/status') return { available: true }
    if (path === '/planner/sessions') return { sessions: [] }
    return draft
  }
  return { calls, planner: createPlanner({ goalId: 'goal', revision: 7, scope: 'current-scope', makeId: () => `new-request-${++requestNumber}`, api }) }
}
test('planner loads history without generating recommendations or altering goals', async () => {
  const { planner, calls } = setup()
  assert.equal(planner.getSnapshot().loading, true)
  await planner.load()
  assert.equal(planner.getSnapshot().loading, false)
  assert.deepEqual(calls.map(c => c[0]), ['/planner/status', '/planner/sessions'])
})
test('request shows pending, sends scope and revision, and retains draft-only results', async () => {
  let release
  const { planner, calls } = setup(path => path === '/planner/assess' ? new Promise(resolve => { release = resolve }) : undefined)
  await planner.load()
  const pending = planner.send('What if I save less?', ['Groceries'])
  assert.equal(planner.getSnapshot().pending, true)
  assert.equal(await planner.send('Duplicate'), false)
  release(draft); await pending
  assert.equal(planner.getSnapshot().pending, false)
  assert.equal(planner.getSnapshot().records[0].draft.activation_allowed, false)
  const [path, request] = calls.at(-1)
  assert.equal(path, '/planner/assess'); assert.equal(request.scope, 'current-scope')
  assert.equal(request.body.expected_revision, 7); assert.deepEqual(request.body.protected_categories, ['Groceries'])
  assert.ok(!calls.some(([p]) => /decision|proposals|transfer|payments/.test(p)))
})
test('follow-up questions use the existing conversation and a hypothetical remains draft only', async () => {
  const { planner, calls } = setup(); await planner.load()
  await planner.send('What if I save $200?'); await planner.send('Explain another option')
  assert.equal(calls.at(-1)[1].body.session_id, 'conversation')
  assert.equal(planner.getSnapshot().records.length, 2)
  planner.selectSession(''); assert.equal(planner.getSnapshot().sessionId, null)
})
test('alternatives display backend computed figures and the recommended option', () => {
  const rows = calculatedOptions(draft)
  assert.equal(rows[0].calculation.plans[0].contribution, 311.17)
  assert.equal(rows[1].calculation.plans[0].contribution, 193.42)
  assert.equal(rows[1].recommended, true)
})
test('provider errors and stale contexts clear pending without inventing a recommendation', async () => {
  const { planner } = setup(path => { if (path === '/planner/assess') throw Object.assign(new Error('Financial state changed; reassess'), { status: 409 }) })
  await planner.load(); assert.equal(await planner.send('Assess'), false)
  assert.match(planner.getSnapshot().error, /state changed/)
  assert.equal(planner.getSnapshot().pending, false); assert.deepEqual(planner.getSnapshot().records, [])
})
test('customer/scenario disposal aborts requests and ignores late responses', async () => {
  let release
  const { planner, calls } = setup(path => path === '/planner/assess' ? new Promise(resolve => { release = resolve }) : undefined)
  await planner.load(); const pending = planner.send('Assess')
  planner.dispose(); release(draft); await pending
  assert.ok(calls.some(([p]) => p.endsWith('/cancel')))
  assert.equal(planner.getSnapshot().records.length, 0)
  assert.deepEqual(planner.getSnapshot().records, [])
})
test('unavailable Gemini keeps banking available and blocks inference requests', async () => {
  const { planner, calls } = setup(path => path === '/planner/status' ? { available: false } : undefined)
  await planner.load(); assert.equal(await planner.send('Assess'), false)
  assert.equal(calls.length, 2)
})
test('history is restricted to the selected goal and includes stale-draft status', async () => {
  const { planner } = setup(path => path === '/planner/sessions' ? { sessions: [{ goal_id: 'other', session_id: 'foreign' }, { goal_id: 'goal', session_id: 'mine', stale: true, draft }] } : undefined)
  await planner.load(); assert.equal(planner.getSnapshot().records.length, 1)
  assert.equal(planner.getSnapshot().sessionId, 'mine')
  assert.equal(planner.getSnapshot().records[0].stale, true)
})

test('background progress distinguishes waiting and does not complete before validated draft', async () => {
  const seen = [], calls = [], jobs = [
    { request_id: 'job', status: 'running', progress: { status: 'waiting', generation_attempts: 2, next_retry_at: 200 } },
    { request_id: 'job', status: 'running', progress: { status: 'generating', generation_attempts: 3 } },
    { request_id: 'job', status: 'completed', draft },
  ]
  const api = async (path, options = {}) => {
    calls.push([path, options])
    if (path === '/planner/status') return { available: true }
    if (path === '/planner/sessions') return { sessions: [] }
    if (path === '/planner/assess') return { request_id: 'job', status: 'queued' }
    return jobs.shift()
  }
  const p = createPlanner({ goalId: 'goal', revision: 7, scope: 'scope', api, makeId: () => 'job', wait: async () => {} })
  p.subscribe(() => seen.push({ pending: p.getSnapshot().pending, progress: p.getSnapshot().job?.progress?.status, records: p.getSnapshot().records.length }))
  await p.load(); assert.equal(await p.send('Assess'), true)
  assert.ok(seen.some(s => s.pending && s.progress === 'waiting' && s.records === 0))
  assert.equal(p.getSnapshot().records.length, 1)
  assert.equal(calls.find(([path]) => path === '/planner/assess')[1].body.background, true)
  assert.ok(calls.filter(([path]) => path.includes('/requests/')).every(([,o]) => o.scope === 'scope'))
})

test('interrupted request resumes explicitly and replaces its row without duplicate drafts', async () => {
  let resumed = false
  const api = async path => {
    if (path === '/planner/status') return { available: true }
    if (path === '/planner/sessions') return { sessions: [] }
    if (path.endsWith('/retry')) { resumed = true; return { status: 'queued' } }
    if (path === '/planner/assess') return { status: 'queued' }
    return resumed ? { status: 'completed', draft } : { status: 'interrupted', resumable: true, session_id: 'conversation', error: { message: 'Retry deadline reached' } }
  }
  const p = createPlanner({ goalId: 'goal', revision: 7, scope: 'scope', api, makeId: () => 'job', wait: async () => {} })
  await p.load(); assert.equal(await p.send('Assess'), false)
  assert.equal(resumed, false); assert.equal(p.getSnapshot().records[0].resumable, true)
  assert.equal(await p.retry('job'), true); assert.equal(p.getSnapshot().records.length, 1)
  assert.equal(p.getSnapshot().records[0].draft.activation_allowed, false)
})

test('cancellation is explicit and a lost status connection does not silently finish the job', async () => {
  const calls = []
  const api = async (path, options = {}) => {
    calls.push([path, options])
    if (path === '/planner/status') return { available: true }
    if (path === '/planner/sessions') return { sessions: [] }
    if (path === '/planner/assess') return { status: 'queued' }
    if (path.endsWith('/cancel')) return { status: 'cancelling' }
    throw new Error('network lost')
  }
  const p = createPlanner({ goalId: 'goal', revision: 7, scope: 'scope', api, makeId: () => 'job', wait: async () => {} })
  await p.load(); await p.send('Assess')
  assert.equal(p.getSnapshot().pending, true)
  assert.equal(p.getSnapshot().job.status, 'connection_lost')
  assert.equal(await p.cancel(), true)
  assert.ok(calls.some(([path, options]) => path.endsWith('/cancel') && options.method === 'POST' && options.keepalive))
  p.dispose()
})


test('lost submission response preserves the request ID and recovers its existing draft', async () => {
  const calls = []
  const api = async path => {
    calls.push(path)
    if (path === '/planner/status') return { available: true }
    if (path === '/planner/sessions') return { sessions: [] }
    if (path === '/planner/assess') throw new Error('response lost after server acceptance')
    return { status: 'completed', draft }
  }
  const p = createPlanner({ goalId: 'goal', revision: 7, scope: 'scope', api, makeId: () => 'retained' })
  await p.load(); await p.send('Original question')
  assert.equal(p.getSnapshot().pending, true)
  assert.equal(p.getSnapshot().requestId, 'retained')
  assert.equal(await p.send('Duplicate request'), false)
  assert.equal(await p.checkStatus(), true)
  assert.equal(p.getSnapshot().records[0].question, 'Original question')
  assert.equal(calls.filter(path => path === '/planner/assess').length, 1)
})
