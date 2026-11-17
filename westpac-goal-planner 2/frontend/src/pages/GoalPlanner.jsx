import { useState } from 'react'
import { useBank, money, day, label } from '../data/mockBank.js'
import ShockEvents from '../components/ShockEvents.jsx'
import GeminiPlanner from '../components/GeminiPlanner.jsx'
import { FinancialFacts, InsightList } from '../components/BankingPanels.jsx'

function GoalCard({ goal }) {
  const { mutate, busy } = useBank()
  const [editing, setEditing] = useState(false)
  return <article className="card pad"><div className="row"><h3>{label(goal.kind)} goal</h3><span className="tag">Version {goal.version}</span></div>
    <p className="small muted">Current approved goal</p><strong>{money(goal.allocated)} allocated of {money(goal.target)}</strong><progress max="100" value={goal.progress_percent} aria-label="Goal progress" />
    <p>Deadline {day(goal.deadline)} · {goal.progress_percent}% funded</p><p className="small muted">Arithmetic only: {goal.contribution_periods} monthly periods, {money(goal.required_monthly)} per period. This is not an AI recommendation.</p>
    <button className="small-btn" onClick={() => setEditing(!editing)}>Propose my own goal change</button>
    {editing && <form onSubmit={async e => { e.preventDefault(); const f = new FormData(e.currentTarget); const ok = await mutate(`/goals/${goal.goal_id}/proposals`, { changes: { target: Number(f.get('target')), allocated: Number(f.get('allocated')), deadline: f.get('deadline') }, source: 'user' }, 'Your manual proposal is ready for review.'); if (ok) setEditing(false) }}>
      <label className="field">New target (AUD)<input name="target" type="number" min="0" step="0.01" required defaultValue={goal.target} /></label>
      <label className="field">Allocated savings (AUD)<input name="allocated" type="number" min="0" step="0.01" required defaultValue={goal.allocated} /></label>
      <label className="field">New deadline<input name="deadline" type="date" required defaultValue={goal.deadline} /></label>
      <button className="primary-btn" disabled={busy}>Create manual proposal</button>
    </form>}
  </article>
}
export default function GoalPlanner({ onBack }) {
  const { goals, proposals, goal_history, mutate, busy, goal_available, scenario } = useBank()
  async function create(e) {
    e.preventDefault(); const f = new FormData(e.currentTarget)
    await mutate('/goals', { kind: f.get('kind'), target: Number(f.get('target')), allocated: Number(f.get('allocated')), deadline: f.get('deadline'), confirmed: true }, 'Goal created.')
  }
  return <div className="page"><header className="pagehead"><button className="backbtn" onClick={onBack}>← Back</button><h1>Goal planner</h1></header><div className="body grey">
    <div className="card pad"><h3>AI proposes. Financial checks inform. You decide.</h3><p>The planner creates provisional drafts with independent financial-check findings. AI drafts cannot be approved or activated, even when the implemented checks pass. Your manual goal workflow remains available.</p>{scenario !== 'customer' && <p className="small muted">The holiday goal is seeded demo data in a separate sandbox.</p>}</div>
    <FinancialFacts />
    <ShockEvents />
    <GeminiPlanner />
    {!goals.length && <p>No goals yet. Create one below.</p>}{goals.map(g => <GoalCard key={`${g.goal_id}-${g.version}`} goal={g} />)}
    <details className="card pad"><summary>Create a goal</summary><form onSubmit={create}>
      <label className="field">Goal type<select name="kind"><option value="holiday">Holiday</option><option value="emergency_fund">Emergency fund</option><option value="purchase">Purchase</option><option value="other">Other</option></select></label>
      <label className="field">Target (AUD)<input required name="target" type="number" min="0" step="0.01" /></label>
      <label className="field">Already allocated (AUD)<input required name="allocated" type="number" min="0" step="0.01" defaultValue="0" /></label>
      <p className="small muted">Total explicitly designated goal savings: {money(goal_available)}. Allocations across goals cannot exceed this pool.</p>
      <label className="field">Deadline<input required name="deadline" type="date" /></label><button disabled={busy} className="primary-btn">Create goal</button>
    </form></details>
    <h2>Proposal review</h2>{!proposals.length && <p>No proposals awaiting review.</p>}{proposals.map(p => <div className="card pad" key={p.proposal_id}><h3>{p.source === 'user' ? 'Your manual change' : 'AI proposal — validation required'}</h3><p>{label(p.status)}</p>
      <p>{Object.entries(p.changes).map(([k, v]) => `${label(k)}: ${k === 'deadline' ? day(v) : money(v)}`).join(' · ')}</p>
      {['pending', 'snoozed'].includes(p.status) && <div className="actions">{['approve', 'reject', 'snooze'].map(decision => <button className="small-btn" key={decision} disabled={busy || (decision === 'approve' && p.source !== 'user')} onClick={() => mutate(`/proposals/${p.proposal_id}/decision`, { decision, confirmed: true }, `Proposal ${decision === 'approve' ? 'approved' : decision === 'reject' ? 'rejected' : 'snoozed'}.`)}>{label(decision)}</button>)}</div>}
      {p.decisions.map((d, i) => <p className="small muted" key={i}>{label(d.decision)} · {day(d.on)}</p>)}
    </div>)}
    <details><summary>Goal change history</summary>{!goal_history.length && <p>No recorded goal changes.</p>}{goal_history.map(g => <div className="card pad" key={`${g.goal_id}-${g.version}`}><strong>{label(g.kind)} · version {g.version}</strong><p>{money(g.target)} target · {money(g.allocated)} allocated · deadline {day(g.deadline)}</p><p className="small muted">Recorded {day(g.recorded_on)}</p></div>)}</details>
    <h2>Relevant insights</h2><InsightList compact />
  </div></div>
}
