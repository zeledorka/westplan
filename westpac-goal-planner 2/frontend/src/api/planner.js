import { request } from './client.js'

const active = new Set(['queued', 'running', 'generating', 'retrying', 'waiting', 'cancelling'])
const pause = (ms, signal) => new Promise(resolve => {
  const done = () => { clearTimeout(timer); signal?.removeEventListener('abort', done); resolve() }
  const timer = setTimeout(done, ms)
  signal?.addEventListener('abort', done, { once: true })
})

export function createPlanner({ goalId, revision, scope, api = request, makeId = () => crypto.randomUUID(), wait = pause }) {
  let state = { loading: true, pending: false, error: '', status: null, records: [], sessionId: null, requestId: null, job: null }
  let alive = true, activeQuestion = ''
  const listeners = new Set(), abort = new AbortController()
  const update = patch => { if (alive) { state = { ...state, ...patch }; listeners.forEach(fn => fn()) } }
  const stop = id => api(`/planner/requests/${encodeURIComponent(id)}/cancel`, { method: 'POST', scope, keepalive: true }).catch(() => null)
  function completed(draft, question, id) {
    const row = { request_id: id, session_id: draft.session_id, goal_id: goalId, question, draft, status: draft.status, stale: !!draft.stale }
    update({ records: [...state.records.filter(r => r.request_id !== id), row], sessionId: draft.session_id, pending: false, requestId: null, job: { status: 'completed' } })
    return alive
  }
  async function follow(id, question, first) {
    let job = first
    try {
      while (alive) {
        job = job || await api(`/planner/requests/${encodeURIComponent(id)}`, { scope, signal: abort.signal })
        update({ job, requestId: id })
        if (job.draft) return completed(job.draft, question, id)
        if (!active.has(job.status)) {
          update({ pending: false, error: job.error?.message || (job.status === 'cancelled' ? 'Planning cancelled.' : ''),
            records: [...state.records.filter(r => r.request_id !== id), { ...job, request_id: id, goal_id: goalId, question, status: job.status }] })
          return false
        }
        update({ pending: true })
        await wait(Math.min(5000, Math.max(1000, job.poll_after_ms || 2500)), abort.signal)
        job = null
      }
      return false
    } catch (e) {
      if (alive && [400, 401, 404, 409].includes(e.status)) { update({ pending: false, requestId: null, error: e.message, job: { status: 'failed' } }); return false }
      if (alive && e.name !== 'AbortError') update({ pending: true, error: 'Progress connection lost. The backend may still be running. Check status or cancel.', job: { ...(job || state.job), status: 'connection_lost' } })
      return false
    }
  }
  return {
    getSnapshot: () => state,
    subscribe(fn) { listeners.add(fn); return () => listeners.delete(fn) },
    dispose() { const id = state.pending && state.requestId; alive = false; abort.abort(); listeners.clear(); if (id) void stop(id) },
    selectSession(sessionId) { update({ sessionId: sessionId || null, error: '' }) },
    async load() {
      try {
        const [status, history] = await Promise.all([api('/planner/status', { signal: abort.signal }), api('/planner/sessions', { signal: abort.signal })])
        const records = history.sessions.filter(r => r.goal_id === goalId)
        update({ status, records, sessionId: records.at(-1)?.session_id || null })
        const running = records.findLast(r => active.has(r.status))
        if (running) { activeQuestion = running.question; update({ pending: true, requestId: running.request_id }); void follow(running.request_id, running.question) }
      } catch (e) { if (e.name !== 'AbortError') update({ error: e.message }) }
      finally { update({ loading: false }) }
    },
    async send(question, protectedCategories = []) {
      if (state.pending || !question.trim() || !state.status?.available) return false
      const id = makeId(); activeQuestion = question
      update({ pending: true, error: '', requestId: id, job: { status: 'queued' } })
      try {
        // Submission returns a job promptly. It is not aborted on unmount before its ID can be cancelled.
        const result = await api('/planner/assess', { method: 'POST', scope, keepalive: true,
          body: { goal_id: goalId, question, request_id: id, expected_revision: revision, background: true,
            session_id: state.sessionId, protected_categories: protectedCategories } })
        if (!alive) { await stop(id); return false }
        if (result.draft_id) return completed(result, question, id) // compatible synchronous test/older backend result
        return await follow(id, question, result)
      } catch (e) {
        if (e.name !== 'AbortError') update(e.status >= 400 && e.status < 500 ? { error: e.message, pending: false, requestId: null } : { error: 'Submission connection lost. The backend may have accepted this request. Check status or cancel.', pending: true, job: { status: 'connection_lost' } })
        return false
      }
    },
    async retry(id) {
      const record = state.records.find(r => r.request_id === id)
      if (state.pending || !record || record.stale || !record.resumable) return false
      activeQuestion = record.question
      update({ pending: true, error: '', requestId: id, job: { status: 'queued' } })
      try {
        const job = await api(`/planner/requests/${encodeURIComponent(id)}/retry`, { method: 'POST', scope, keepalive: true, body: { expected_revision: revision } })
        if (!alive) { await stop(id); return false }
        return await follow(id, record.question, job)
      } catch (e) { update(e.status >= 400 && e.status < 500 ? { pending: false, error: e.message } : { pending: true, error: 'Resume connection lost. Check the existing request status before retrying.', job: { status: 'connection_lost' } }); return false }
    },
    async cancel(id = state.requestId) {
      if (!id || !state.pending && !state.records.some(r => r.request_id === id && r.resumable)) return false
      const result = await stop(id)
      if (!result) { update({ error: 'Cancellation could not be confirmed. Check the request status.' }); return false }
      if (!state.pending) { update({ records: state.records.map(r => r.request_id === id ? { ...r, status: 'cancelled', resumable: false, error: { message: 'Interrupted assessment discarded.' } } : r) }); return true }
      update({ job: { ...(state.job || {}), status: result.status === 'draft_unverified' ? 'retrieving_completed' : 'cancelling' } })
      return true
    },
    async checkStatus() {
      if (!state.requestId) return false
      const record = state.records.find(r => r.request_id === state.requestId)
      update({ error: '' }); return follow(state.requestId, record?.question || activeQuestion)
    },
  }
}

export function planningProgress(job) {
  const status = job?.status === 'connection_lost' ? 'connection_lost' : job?.progress?.status || job?.status
  if (job?.cancel_requested || status === 'cancelling') return 'Cancellation requested. The current Google call may take up to 30 seconds to stop.'
  if (status === 'retrieving_completed') return 'The assessment already finished. Retrieving its validated draft.'
  if (status === 'waiting') return `Gemini is temporarily unavailable. Waiting before the next attempt${job.progress?.next_retry_at ? ` at ${new Date(job.progress.next_retry_at * 1000).toLocaleTimeString()}` : ''}.`
  if (status === 'retrying') return 'Gemini is temporarily unavailable. Retrying the current step; completed calculations are preserved.'
  if (status === 'interrupted') return 'Planning was interrupted by provider availability or its finite deadline. You can explicitly resume it.'
  if (status === 'connection_lost') return 'Progress connection lost. The backend may still be running.'
  return 'Gemini is generating and validating a draft using Python calculations.'
}

export function calculatedOptions(draft) {
  return draft.result.strategies.map(option => ({ ...option, recommended: option.calculation_ref === draft.result.recommended_ref,
    calculation: draft.calculations[option.calculation_ref] }))
}
