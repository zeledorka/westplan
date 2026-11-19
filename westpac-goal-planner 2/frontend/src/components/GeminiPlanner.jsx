import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { useBank, money, day, label } from '../data/mockBank.js'
import { request } from '../api/client.js'
import { createPlanner, calculatedOptions, planningProgress, safetySummary, safetyRevisionMessage } from '../api/planner.js'

function Items({ title, values }) {
  return !!values?.length && <div><strong>{title}</strong><ul>{values.map((v,i) => <li key={i}>{v}</li>)}</ul></div>
}
function Safety({ assessment, stale = false, compact = false }) {
  const summary = safetySummary(stale ? null : assessment)
  return <div className="card pad" aria-label="Independent financial checks">
    <strong>{summary.label}</strong>
    {!compact && <p className="small">{summary.status === 'PASS' ? 'Passes the implemented checks under the recorded assumptions; this does not guarantee an optimal or affordable future plan.' : summary.status === 'REVISE' ? 'The proposal needs changes or more funding evidence before it can pass.' : 'More information or a revised assessment is needed before this proposal can proceed.'}</p>}
    {compact ? <details><summary>Financial findings and limits</summary><Items values={summary.findings.map(f=>f.message)} /><p className="small">Conditional on recorded assumptions; not a guarantee of future affordability.</p></details> : <Items title="Financial findings" values={summary.findings.map(f => f.message)} />}
  </div>
}
function Draft({ record, compact = false }) {
  const { revision, scope_version, scenario, mutate, busy } = useBank()
  const [decision, setDecision] = useState(record.draft?.review_decision || null)
  const [decisionError,setDecisionError]=useState('')
  const [selectedCalculation,setCalculation]=useState('')
  const draft = record.draft
  if (!draft) return <div><p role="alert">{record.status === 'interrupted' ? (record.progress?.reason === 'deadline_reached' ? 'Planning deadline reached' : 'Planning interrupted') : record.status === 'cancelled' ? 'Planning cancelled' : 'Previous request failed'}: {record.error?.message}</p>{record.safety_revision && <><p>{safetyRevisionMessage(record.safety_revision)}</p><Safety assessment={record.safety_revision.history?.[0]?.safety_assessment} stale={record.stale} /></>}</div>
  const eligible=calculatedOptions(draft).filter(o=>o.safety?.status==='PASS')
  const chosen=selectedCalculation || eligible.find(o=>o.recommended)?.calculation_ref || eligible[0]?.calculation_ref || ''
  const canApprove=scenario==='integrated_demo' && !record.stale && draft.safety_assessment?.status==='PASS' && !!chosen
  const approved=decision?.decision==='approved'
  return <article className="card pad planner-draft">
    <div className="row"><h3>{draft.model_generated ? `${draft.metadata?.provider === 'openrouter' ? 'OpenRouter' : 'Gemini'} assessment` : 'Simulated planner test output'}</h3><span className="tag">{approved ? 'Approved in demo' : 'Provisional draft'}</span></div>
    {!compact && <p className="small muted">Production activation remains disabled. In the isolated presenter sandbox, a human may apply an independently passing calculated schedule. The planner can make one bounded safety revision; financial changes require Python calculations.</p>}
    {draft.safety_revision && <>{!compact && <p role="status">{safetyRevisionMessage(draft.safety_revision)}</p>}<details><summary>Safety revision history · {draft.safety_revision.revision_count} automatic revision(s)</summary>{draft.safety_revision.history.map((entry,i) => <p key={i}>{i === 0 ? 'Original assessment' : 'Revised assessment'}: {entry.safety_assessment.status}. New calculations: {entry.new_calculation_refs.join(', ') || 'none'}.</p>)}</details></>}
    {record.stale && !approved && <p role="alert">Financial facts have changed. This is an older draft; request a fresh assessment.</p>}
    {record.stale && approved ? <div className="card pad" role="status"><strong>Historical approval · independent PASS</strong><p className="small">Checked again when you accepted. This records that approval; it does not certify later financial changes.</p></div> : <Safety assessment={draft.safety_assessment} stale={record.stale} compact={compact} />}
    <p><strong>{label(draft.result.assessment)}</strong> · {draft.result.change_recommended ? 'A change is suggested for review' : 'No change recommended without further review'}</p>
    {compact ? <details><summary>{draft.result.strategies.find(s=>s.calculation_ref===draft.result.recommended_ref)?.title || 'Read the AI recommendation'}</summary><p>{draft.result.answer}</p></details> : <p>{draft.result.answer}</p>}
    <section className="card pad"><h3>Human review</h3>
    {decision ? <div role="status"><strong>{decision.decision === 'approved' ? 'Approved and applied in the sandbox.' : 'Rejection persisted. The original goal is unchanged.'}</strong>{decision.after && <p>Contribution: {money(decision.before.contribution)} → {money(decision.after.contribution)} {decision.after.frequency}; deadline {day(decision.before.deadline)} → {day(decision.after.deadline)}.</p>}</div> : <>
      {scenario==='integrated_demo' && eligible.length>0 && <label className="field">Calculated adjustment to approve<select value={chosen} onChange={e=>setCalculation(e.target.value)}>{eligible.map(o=><option key={o.calculation_ref} value={o.calculation_ref}>{o.title} · {o.calculation_ref}</option>)}</select></label>}
      {compact && chosen && draft.calculations[chosen]?.plans.map(plan=><p className="small" key={plan.goal_ref}><strong>{money(plan.contribution)} {plan.frequency}</strong> · target {money(plan.target)} · deadline {day(plan.deadline)}{plan.pause_months ? ` · pause ${plan.pause_months} months` : ''}</p>)}
      <button className="primary-btn" disabled={busy || !canApprove} onClick={async()=>{setDecisionError('');if(await mutate(`/presenter/drafts/${draft.draft_id}/approve`,{calculation_ref:chosen,confirmed:true},'AI adjustment approved and applied in the isolated sandbox.'))setDecision({decision:'approved'})}}>{scenario==='integrated_demo' ? 'Accept · demo only' : 'Approve calculated adjustment'}</button>
      {!canApprove && <p className="small">{scenario!=='integrated_demo' ? 'Approval is only enabled in the isolated presenter sandbox.' : record.stale ? 'This assessment is stale. Run a new assessment.' : 'Approval requires overall and selected-option PASS. BLOCK or unresolved REVISE cannot be overridden.'}</p>}
      <button className="outline-btn" onClick={async()=>{try{const saved=await request(`/planner/drafts/${draft.draft_id}/reject`,{method:'POST',scope:scope_version,body:{expected_revision:revision,confirmed:true}});setDecision(saved)}catch(e){setDecisionError(e.message)}}}>{scenario==='integrated_demo' ? 'Reject · demo only' : 'Reject AI proposal'}</button>
    </>}{decisionError && <p role="alert">{decisionError}</p>}</section>
    <details open={!compact}><summary>Calculation details, alternatives and assumptions</summary>
    <Items title="Questions before deciding" values={draft.result.missing_information} />
    {calculatedOptions(draft).map(option => <section className="card pad" key={option.calculation_ref}>
      <h3>{option.title}{option.recommended ? ' · Suggested by the model' : ''}</h3><p>{option.approach}</p><p>{option.rationale}</p>
      <Safety assessment={option.safety} stale={record.stale} />
      <p className="small muted">Hypothetical option. Figures below are calculated by Python; The model’s explanation remains unverified.</p>
      {option.calculation.plans.map(plan => <div key={plan.goal_ref}>
        <strong>{label(plan.goal_kind)} goal</strong>
        <p>{money(plan.contribution)} {plan.frequency} · {plan.pause_months ? `after a ${plan.pause_months}-month pause` : 'starting next contribution period'}</p>
        <p>Target {money(plan.target)} · deadline {day(plan.deadline)}</p>
        <p>Required by this date: {money(plan.required_per_period)} per period. Next contribution: {day(plan.next_contribution_date)}. Estimated completion: {day(plan.completion_date)}.</p>
        <p className="small">{plan.meets_deadline ? 'Reaches the target under these assumptions.' : 'Does not reach the target by this deadline.'}</p>
      </div>)}
      <p>Across all goals: {money(option.calculation.aggregate_monthly_equivalent)} monthly equivalent. Comfortable historical capacity: {money(option.calculation.comfortable_capacity)}. Stretch estimate: {money(option.calculation.stretch_capacity)}.</p>
      <p>Projected cash after known obligations, before proposed contributions: {money(option.calculation.current_projected_cash_after_known_obligations)}.</p>
      {option.calculation.one_month_comparison && <p>Legacy scalar comparison (not approval evidence): {money(option.calculation.one_month_comparison.liquid_after_pending_reserve_and_contribution)}. Monthly flow remaining after contributions: {money(option.calculation.one_month_comparison.monthly_surplus_after_contribution)}.</p>}
      {option.calculation.dated_cashflow?.available && <p>Dated minimum cash: {money(option.calculation.dated_cashflow.minimum_cash)} on {day(option.calculation.dated_cashflow.minimum_date)}, forecast through {day(option.calculation.dated_cashflow.end)}. Includes estimated income and ordinary spending.</p>}
      <Items title="Proposed actions — not applied" values={option.proposed_actions} />
      <Items title="Benefits" values={option.benefits} /><Items title="Trade-offs" values={option.drawbacks} />
      <details><summary>Assumptions and calculation limits</summary><Items values={[...option.assumptions, ...option.calculation.assumptions]} /></details>
    </section>)}
    <Items title="Assumptions" values={draft.result.assumptions} />
    </details>

  </article>
}
function Conversation({ goal, compact = false, autoRequest, onAutoStarted }) {
  const { revision, scope_version, busy, refresh, notify } = useBank()
  const planner = useMemo(() => createPlanner({ goalId: goal.goal_id, revision, scope: scope_version }), [goal.goal_id, revision, scope_version])
  const state = useSyncExternalStore(planner.subscribe, planner.getSnapshot)
  const [question, setQuestion] = useState('Can this goal still be achieved while protecting essential spending and emergency savings?')
  const [protectGroceries, setProtectGroceries] = useState(false)
  useEffect(() => { planner.load(); return () => planner.dispose() }, [planner])
  const autoStarted=useRef(null)
  useEffect(()=>{
    if (!autoRequest || autoRequest.goalId!==goal.goal_id || autoStarted.current===autoRequest.eventId || state.loading || state.pending || !state.status?.available) return
    autoStarted.current=autoRequest.eventId
    void planner.assessEvent(autoRequest)
    onAutoStarted?.()
  },[autoRequest,goal.goal_id,state.loading,state.pending,state.status?.available,planner,onAutoStarted])
  const sessions = [...new Set(state.records.map(r => r.session_id))]
  const visibleRecords=compact ? state.records.filter(r=>r.session_id===state.sessionId).slice(-1) : state.records.filter(r=>r.session_id===state.sessionId)
  const latest=visibleRecords.at(-1)
  useEffect(()=>{
    if (!compact || state.pending || !notify) return
    if (state.job?.status==='completed' && latest?.draft) notify(`AI assessment complete · ${latest.draft.safety_assessment?.status || 'Not assessed'}. Review the recommendation.`)
    else if (state.error) notify('')
  },[compact,state.pending,state.job?.status,latest?.draft,state.error,notify])
  const progress=state.pending && <div className="card pad" role="status"><strong>AI assessment in progress</strong><p>{planningProgress(state.job)}</p><p className="small">Attempt {state.job?.progress?.generation_attempts || '…'} · {state.job?.progress?.completed_calculations || 0} saved calculations</p><progress aria-label="Planning in progress" /><button type="button" onClick={()=>planner.cancel()}>Cancel planning</button>{state.job?.status==='connection_lost' && <button type="button" onClick={()=>planner.checkStatus()}>Check status</button>}</div>
  return <section className="planner-conversation">
    {state.loading && <p role="status">Loading planning conversations…</p>}
    {state.status && !state.status.available && <p role="status">The selected model provider is unavailable. Configure the backend key to enable AI planning. Banking and manual goal changes remain available. <button className="link" onClick={()=>planner.load()}>Retry provider status</button></p>}
    {progress}
    {!compact && !state.pending && latest?.draft && <p className="status success" role="status">AI assessment complete · {latest.draft.safety_assessment?.status || 'Not assessed'}. Review the recommendation below.</p>}
    {compact && !state.pending && !latest && <p className="small muted">An AI assessment starts automatically after you record an expense above.</p>}
    {!compact && <label className="field">Planning conversation<select value={state.sessionId || ''} disabled={state.pending} onChange={e => planner.selectSession(e.target.value)}>
      <option value="">New conversation</option>{sessions.map((id,i) => <option key={id} value={id}>Conversation {i+1}</option>)}
    </select></label>}
    {!state.pending && visibleRecords.map((r,i) => <div key={r.request_id || i}>{compact ? <details><summary>Assessment request</summary><p>{r.question}</p></details> : <p className="planner-question"><strong>You:</strong> {r.question}</p>}<Draft record={r} compact={compact} />{!r.draft && !r.resumable && <button type="button" disabled={busy || state.pending || !state.status?.available} onClick={()=>planner.send(r.question)}>Retry assessment with current facts</button>}{r.resumable && !r.stale && <button type="button" disabled={state.pending} onClick={() => planner.retry(r.request_id)}>Resume interrupted assessment</button>}{r.resumable && <button type="button" disabled={state.pending} onClick={() => planner.cancel(r.request_id)}>Discard interrupted assessment</button>}</div>)}
    <details open={!compact || !!state.error}><summary>{state.error ? 'Retry or ask a follow-up' : 'Ask a follow-up or view conversations'}</summary><form className="card pad" onSubmit={async e => { e.preventDefault(); if (await planner.send(question, protectGroceries ? ['Groceries'] : [])) setQuestion('') }}>
      <label className="field">Ask about this goal<textarea rows="3" maxLength="1600" required value={question} onChange={e => setQuestion(e.target.value)} placeholder="Ask about affordability, an alternative, or a hypothetical contribution…" /></label>
      <label className="small"><input type="checkbox" checked={protectGroceries} onChange={e => setProtectGroceries(e.target.checked)} /> Keep grocery spending protected in this conversation</label>
      <p className="small muted">Your question and minimised financial context go to {state.status?.provider === 'openrouter' ? 'OpenRouter and its configured model provider' : 'Google Gemini'}. Leave out names, account numbers and credentials. Hypothetical questions do not change your plan.</p>
      <button className="primary-btn" disabled={busy || state.loading || state.pending || !state.status?.available}>{state.pending ? 'Analysing and calculating…' : state.error ? 'Retry assessment with current facts' : 'Ask the planner — draft only'}</button>

      {state.error && <p role="alert">{state.error} <button type="button" className="link" onClick={refresh}>Refresh financial facts</button></p>}
    </form></details>
  </section>
}
export default function GeminiPlanner({ compact = false, autoRequest, onAutoStarted }) {
  const { goals } = useBank()
  const [selected, setSelected] = useState('')
  useEffect(()=>{const focus=e=>setSelected(e.detail);window.addEventListener('westplan-assess-goal',focus);return()=>window.removeEventListener('westplan-assess-goal',focus)},[])
  useEffect(()=>{if(autoRequest) setSelected(autoRequest.goalId)},[autoRequest])
  const goal = goals.find(g => g.goal_id === selected) || goals[0]
  return <section><h2>AI goal planner</h2>{!compact && <p>Explore strategies and follow-up questions. All AI results remain provisional drafts.</p>}
    {!goal ? <p>Create a goal above to request an assessment.</p> : <>
      {!compact && <label className="field">Goal to assess<select value={goal.goal_id} onChange={e => setSelected(e.target.value)}>{goals.map(g => <option key={g.goal_id} value={g.goal_id}>{label(g.kind)} · {money(g.target)} · {day(g.deadline)}</option>)}</select></label>}
      <Conversation key={goal.goal_id} goal={goal} compact={compact} autoRequest={autoRequest} onAutoStarted={onAutoStarted} />
    </>}
  </section>
}
