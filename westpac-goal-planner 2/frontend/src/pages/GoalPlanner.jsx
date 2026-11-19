import { useEffect, useState } from 'react'
import { request } from '../api/client.js'
import GoalAssessment from '../components/GoalAssessment.jsx'
import SavingsFunding from '../components/SavingsFunding.jsx'
import { useBank, money, day, label } from '../data/mockBank.js'
import IntegratedPresenter from '../components/IntegratedPresenter.jsx'
import ShockEvents from '../components/ShockEvents.jsx'
import GeminiPlanner from '../components/GeminiPlanner.jsx'
import { FinancialFacts, InsightList } from '../components/BankingPanels.jsx'

function GoalCard({ goal }) {
  const { mutate, busy } = useBank()
  const [editing, setEditing] = useState(false)
  return <article className="card pad"><div className="row"><h3>{label(goal.kind)} goal</h3><span className="tag">Version {goal.version}</span></div>
    <p className="small muted">{goal.assessment_current ? 'Safety-assessed goal · PASS under current recorded assumptions' : goal.financial_assessment ? 'Previously assessed goal · current feasibility needs reassessment' : 'Legacy goal · no recorded creation safety assessment'}</p><strong>{money(goal.allocated)} allocated of {money(goal.target)}</strong><progress max="100" value={goal.progress_percent} aria-label="Goal progress" />
    <p>{goal.contribution != null && <>Recorded contribution {money(goal.contribution)} {goal.frequency} · pause {goal.pause_months || 0} months · </>}Deadline {day(goal.deadline)} · {goal.progress_percent}% funded</p><p className="small muted">{goal.financial_assessment ? `Assessed schedule: ${goal.contribution_periods} ${goal.frequency} periods; required ${money(goal.required_monthly)} per period. ${goal.target === goal.allocated ? (goal.assessment_current ? 'Already funded; no new contributions required.' : 'Recorded allocation covers the target; current backing needs reassessment.') : 'Future contributions are required.'}` : 'No authoritative assessed contribution schedule is recorded.'}</p>
    {goal.contribution != null && <p><strong>{goal.assessment_current ? 'Assessed schedule' : 'Recorded schedule · not currently certified'}: {money(goal.contribution)} {goal.frequency}</strong> · pause {goal.pause_months || 0} month(s) · effective {day(goal.schedule_as_of)}</p>}
    {goal.financial_assessment && <details><summary>Recorded financial assessment</summary><GoalAssessment assessment={goal.financial_assessment} historical={!goal.assessment_current} /></details>}
    <button className="small-btn" onClick={() => setEditing(!editing)}>Propose my own goal change</button>
    {editing && <form onSubmit={async e => { e.preventDefault(); const f = new FormData(e.currentTarget); const ok = await mutate(`/goals/${goal.goal_id}/proposals`, { changes: { target: Number(f.get('target')), allocated: Number(f.get('allocated')), deadline: f.get('deadline'), frequency:f.get('frequency'), ...(f.get('contribution') === '' ? {} : {contribution:Number(f.get('contribution'))}) }, source: 'user' }, 'Your manual proposal is ready for review.'); if (ok) setEditing(false) }}>
      <label className="field">New target (AUD)<input name="target" type="number" min="0" step="0.01" required defaultValue={goal.target} /></label>
      <label className="field">Allocated savings (AUD)<input name="allocated" type="number" min="0" step="0.01" required defaultValue={goal.allocated} /></label>
      <label className="field">New deadline<input name="deadline" type="date" required defaultValue={goal.deadline} /></label>
      <label className="field">Contribution (optional; blank recalculates required amount)<input name="contribution" type="number" min="0" step="0.01" /></label>
      <label className="field">Frequency<select name="frequency" defaultValue={goal.frequency || 'monthly'}><option value="monthly">Monthly</option><option value="fortnightly">Fortnightly</option></select></label>
      <button className="primary-btn" disabled={busy}>Create manual proposal</button>
    </form>}
  </article>
}
export default function GoalPlanner({ onBack, onDemo }) {
  const { goals, proposals, goal_history, mutate, busy, goal_available, scenario, revision, scope_version } = useBank()
  const [creating, setCreating] = useState(false)
  const [autoRequest, setAutoRequest] = useState(null)
  const [workflowActive, setWorkflowActive] = useState(false)
  useEffect(()=>{document.querySelector('main.screen')?.scrollTo({top:0,behavior:'smooth'})},[goals.length,workflowActive])
  const [preview, setPreview] = useState(null)
  const [previewError, setPreviewError] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)
  async function create(e) {
    e.preventDefault(); const f = new FormData(e.currentTarget)
    if (e.nativeEvent.submitter?.value === 'preview') {
      setPreviewBusy(true); setPreviewError(''); setPreview(null)
      try { setPreview(await request('/goals/preview',{method:'POST',scope:scope_version,body:{kind:f.get('kind'),target:Number(f.get('target')),allocated:Number(f.get('allocated')),deadline:f.get('deadline'),frequency:f.get('frequency'),...(f.get('contribution')===''?{}:{contribution:Number(f.get('contribution'))}),confirmed:true,expected_revision:revision}})) }
      catch (error) { setPreviewError(error.message) }
      finally { setPreviewBusy(false) }
      return
    }
    setPreview(null)
    if (await mutate('/goals', { kind: f.get('kind'), target: Number(f.get('target')), allocated: Number(f.get('allocated')), deadline: f.get('deadline'), frequency:f.get('frequency'), ...(f.get('contribution')===''?{}:{contribution:Number(f.get('contribution'))}), confirmed: true }, 'Goal created and safety-assessed. Your approved schedule is shown below.')) setCreating(false)
  }
  return <div className="page"><header className="pagehead"><button className="backbtn" onClick={onBack}>← Back</button><h1>Goal planning</h1></header><div className="body grey">
    {scenario !== 'integrated_demo' && <div className="row"><span className="small">Financial checks apply to every goal.</span><button className="small-btn" onClick={onDemo}>Use isolated demo</button></div>}
    {(!goals.length || creating) && <>
    <section className="card pad goal-create"><h2>Create a goal</h2><details key={revision}><summary>Savings designation · {money(goal_available)} available for goals</summary><SavingsFunding /></details><form className="goal-create-form" onSubmit={create} onChange={() => setPreview(null)}>
      <label className="field">Goal type<select name="kind"><option value="holiday">Holiday</option><option value="emergency_fund">Emergency fund</option><option value="purchase">Purchase</option><option value="other">Other</option></select></label>
      <label className="field">Target (AUD)<input required name="target" type="number" min="0" step="0.01" /></label>
      <label className="field">Already allocated (AUD)<input required name="allocated" type="number" min="0" step="0.01" defaultValue="0" /></label>
      <label className="field">Deadline<input required name="deadline" type="date" /></label>
      <label className="field">Contribution frequency<select name="frequency" defaultValue="monthly"><option value="monthly">Monthly</option><option value="fortnightly">Fortnightly</option></select></label>
      <label className="field">Contribution (optional)<input name="contribution" type="number" min="0" step="0.01" placeholder="Calculate required amount" /></label>
      <p className="small muted">Total explicitly designated goal savings: {money(goal_available)}. Allocations across goals cannot exceed this pool.</p>
      <button name="action" value="preview" disabled={busy || previewBusy}>Check feasibility</button><button name="action" value="create" className="primary-btn" disabled={busy || previewBusy}>Assess and create goal</button>
    </form>{previewError && <p role="alert">{previewError}</p>}<GoalAssessment assessment={preview} />{goals.length>0 && <button className="link" onClick={()=>setCreating(false)}>Return to existing goals</button>}</section>
    </>}
    {!creating && goals.length>0 && <>
      {scenario === 'integrated_demo' ? <IntegratedPresenter collapsed={workflowActive} onAssessment={request=>{setWorkflowActive(true);setAutoRequest(request)}} /> : goals.map(g=><GoalCard key={`${g.goal_id}-${g.version}`} goal={g} />)}
      <div id="ai-planner"><GeminiPlanner compact={scenario==='integrated_demo'} autoRequest={autoRequest} onAutoStarted={()=>setAutoRequest(null)} /></div>
      {!workflowActive && <button className="small-btn" disabled={busy} onClick={()=>setCreating(true)}>Create another goal</button>}
    </>}
    <details className="card pad"><summary>Banking insights, funding and goal tools</summary>
      <button className="small-btn" disabled={busy} onClick={()=>setCreating(true)}>Create another goal</button>
      <FinancialFacts />
      <SavingsFunding />
      <ShockEvents />
      {scenario==='integrated_demo' && goals.map(g=><GoalCard key={`${g.goal_id}-${g.version}`} goal={g} />)}
    <h2>Proposal review</h2>{!proposals.length && <p>No proposals awaiting review.</p>}{proposals.map(p => <div className="card pad" key={p.proposal_id}><h3>{p.source === 'user' ? 'Your manual change' : 'AI proposal — validation required'}</h3><p>{label(p.status)}</p>
      <p>{Object.entries(p.changes).map(([k, v]) => `${label(k)}: ${k === 'deadline' ? day(v) : ['target','allocated','contribution'].includes(k) ? money(v) : v}`).join(' · ')}</p>
      {['pending', 'snoozed'].includes(p.status) && <div className="actions">{['approve', 'reject', 'snooze'].map(decision => <button className="small-btn" key={decision} disabled={busy || (decision === 'approve' && p.source !== 'user')} onClick={() => mutate(`/proposals/${p.proposal_id}/decision`, { decision, confirmed: true }, `Proposal ${decision === 'approve' ? 'approved' : decision === 'reject' ? 'rejected' : 'snoozed'}.`)}>{label(decision)}</button>)}</div>}
      {p.decisions.map((d, i) => <p className="small muted" key={i}>{label(d.decision)} · {day(d.on)}</p>)}
    </div>)}
    <details><summary>Goal change history</summary>{!goal_history.length && <p>No recorded goal changes.</p>}{goal_history.map(g => <div className="card pad" key={`${g.goal_id}-${g.version}`}><strong>{label(g.kind)} · version {g.version}</strong><p>{money(g.target)} target · {money(g.allocated)} allocated · deadline {day(g.deadline)}</p><p className="small muted">Recorded {day(g.recorded_on)}</p></div>)}</details>
    <h2>Relevant insights</h2><InsightList compact />
    </details>
  </div></div>
}
