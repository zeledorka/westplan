import { useEffect, useMemo, useState, useSyncExternalStore } from 'react'
import { useBank, money, day, label } from '../data/mockBank.js'
import { createPlanner, calculatedOptions, planningProgress } from '../api/planner.js'

function Items({ title, values }) {
  return !!values?.length && <div><strong>{title}</strong><ul>{values.map((v,i) => <li key={i}>{v}</li>)}</ul></div>
}
function Draft({ record }) {
  const draft = record.draft
  if (!draft) return <p role="alert">{record.status === 'interrupted' ? (record.progress?.reason === 'deadline_reached' ? 'Planning deadline reached' : 'Planning interrupted') : record.status === 'cancelled' ? 'Planning cancelled' : 'Previous request failed'}: {record.error?.message}</p>
  return <article className="card pad planner-draft">
    <div className="row"><h3>{draft.model_generated ? 'Gemini assessment' : 'Simulated planner test output'}</h3><span className="tag">Provisional draft</span></div>
    <p className="small muted">This draft cannot be activated. The independent Safety Checker is not implemented.</p>
    {record.stale && <p role="alert">Financial facts have changed. This is an older draft; request a fresh assessment.</p>}
    <p><strong>{label(draft.result.assessment)}</strong> · {draft.result.change_recommended ? 'A change is suggested for review' : 'No change recommended without further review'}</p>
    <p>{draft.result.answer}</p>
    <Items title="Questions before deciding" values={draft.result.missing_information} />
    {calculatedOptions(draft).map(option => <section className="card pad" key={option.calculation_ref}>
      <h3>{option.title}{option.recommended ? ' · Suggested option' : ''}</h3><p>{option.approach}</p><p>{option.rationale}</p>
      <p className="small muted">Hypothetical option. Figures below are calculated by Python; Gemini’s explanation remains unverified.</p>
      {option.calculation.plans.map(plan => <div key={plan.goal_ref}>
        <strong>{label(plan.goal_kind)} goal</strong>
        <p>{money(plan.contribution)} {plan.frequency} · {plan.pause_months ? `after a ${plan.pause_months}-month pause` : 'starting next contribution period'}</p>
        <p>Target {money(plan.target)} · deadline {day(plan.deadline)}</p>
        <p>Required by this date: {money(plan.required_per_period)} per period. Estimated completion: {day(plan.completion_date)}.</p>
        <p className="small">{plan.meets_deadline ? 'Reaches the target under these assumptions.' : 'Does not reach the target by this deadline.'}</p>
      </div>)}
      <p>Across all goals: {money(option.calculation.aggregate_monthly_equivalent)} monthly equivalent. Comfortable historical capacity: {money(option.calculation.comfortable_capacity)}.</p>
      <p>Projected cash after known obligations, before proposed contributions: {money(option.calculation.current_projected_cash_after_known_obligations)}.</p>
      <Items title="Proposed actions — not applied" values={option.proposed_actions} />
      <Items title="Benefits" values={option.benefits} /><Items title="Trade-offs" values={option.drawbacks} />
      <details><summary>Assumptions and calculation limits</summary><Items values={[...option.assumptions, ...option.calculation.assumptions]} /></details>
    </section>)}
    <Items title="Assumptions" values={draft.result.assumptions} />
  </article>
}
function Conversation({ goal }) {
  const { revision, scope_version, busy, refresh } = useBank()
  const planner = useMemo(() => createPlanner({ goalId: goal.goal_id, revision, scope: scope_version }), [goal.goal_id, revision, scope_version])
  const state = useSyncExternalStore(planner.subscribe, planner.getSnapshot)
  const [question, setQuestion] = useState('Can this goal still be achieved while protecting essential spending and emergency savings?')
  const [protectGroceries, setProtectGroceries] = useState(false)
  useEffect(() => { planner.load(); return () => planner.dispose() }, [planner])
  const sessions = [...new Set(state.records.map(r => r.session_id))]
  return <section className="planner-conversation">
    {state.loading && <p role="status">Loading planning conversations…</p>}
    {state.status && !state.status.available && <p role="status">Gemini is unavailable. Configure the backend key to enable AI planning. Banking and manual goal changes remain available.</p>}
    <label className="field">Planning conversation<select value={state.sessionId || ''} disabled={state.pending} onChange={e => planner.selectSession(e.target.value)}>
      <option value="">New conversation</option>{sessions.map((id,i) => <option key={id} value={id}>Conversation {i+1}</option>)}
    </select></label>
    {state.records.filter(r => r.session_id === state.sessionId).map((r,i) => <div key={r.request_id || i}><p className="planner-question"><strong>You:</strong> {r.question}</p><Draft record={r} />{r.resumable && !r.stale && <button type="button" disabled={state.pending} onClick={() => planner.retry(r.request_id)}>Resume interrupted assessment</button>}{r.resumable && <button type="button" disabled={state.pending} onClick={() => planner.cancel(r.request_id)}>Discard interrupted assessment</button>}</div>)}
    <form className="card pad" onSubmit={async e => { e.preventDefault(); if (await planner.send(question, protectGroceries ? ['Groceries'] : [])) setQuestion('') }}>
      <label className="field">Ask Gemini about this goal<textarea rows="3" maxLength="1600" required value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about affordability, an alternative, or a hypothetical contribution…" /></label>
      <label className="small"><input type="checkbox" checked={protectGroceries} onChange={e => setProtectGroceries(e.target.checked)} /> Keep grocery spending protected in this conversation</label>
      <p className="small muted">Your question and minimised financial context go to Google Gemini. Leave out names, account numbers and credentials. Hypothetical questions do not change your plan.</p>
      <button className="primary-btn" disabled={busy || state.loading || state.pending || !state.status?.available}>{state.pending ? 'Gemini is analysing and calculating…' : 'Ask Gemini — draft only'}</button>
      {state.pending && <div role="status"><p>{planningProgress(state.job)}</p><p className="small">Attempt {state.job?.progress?.generation_attempts || '…'} · {state.job?.progress?.completed_calculations || 0} saved calculations · Results remain unverified until full validation completes.</p><progress aria-label="Planning in progress" /><button type="button" onClick={() => planner.cancel()}>Cancel planning</button>{state.job?.status === 'connection_lost' && <button type="button" onClick={() => planner.checkStatus()}>Check status</button>}</div>}
      {state.error && <p role="alert">{state.error} <button type="button" className="link" onClick={refresh}>Refresh financial facts</button></p>}
    </form>
  </section>
}
export default function GeminiPlanner() {
  const { goals } = useBank()
  const [selected, setSelected] = useState('')
  const goal = goals.find(g => g.goal_id === selected) || goals[0]
  return <section><h2>Plan with Gemini</h2><p>Explore strategies and follow-up questions. All Gemini results remain provisional drafts.</p>
    {!goal ? <p>Create a goal below to request an assessment.</p> : <>
      <label className="field">Goal to assess<select value={goal.goal_id} onChange={e => setSelected(e.target.value)}>{goals.map(g => <option key={g.goal_id} value={g.goal_id}>{label(g.kind)} · {money(g.target)} · {day(g.deadline)}</option>)}</select></label>
      <Conversation key={goal.goal_id} goal={goal} />
    </>}
  </section>
}
