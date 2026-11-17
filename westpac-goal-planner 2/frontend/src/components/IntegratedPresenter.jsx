import { useState } from 'react'
import { useBank, money, day } from '../data/mockBank.js'

export default function IntegratedPresenter() {
  const bank=useBank()
  const [goalId,setGoal]=useState('')
  const [amount,setAmount]=useState('1500')
  const [kind,setKind]=useState('car_repair')
  const [panel,setPanel]=useState(true)
  const [read,setRead]=useState([])
  const [before,setBefore]=useState(null)
  const selected=bank.goals.find(g=>g.goal_id===goalId) || bank.goals[0]
  const notifications=bank.notifications || []
  const stable=bank.presenter_fixture==='stable_holiday'
  const deadline=`${Number(bank.as_of.slice(0,4))+2}${bank.as_of.slice(4)}`
  function open(notification) {
    setRead(r=>[...r,notification.id]);setGoal(notification.goal_id)
    window.dispatchEvent(new CustomEvent('westplan-assess-goal',{detail:notification.goal_id}))
    document.getElementById('ai-planner')?.scrollIntoView({behavior:'smooth'})
  }
  return <section className="card pad"><span className="badge">Integrated demo · real backend</span><h2>Presenter controls</h2>
    <p>Isolated copy of this customer's actual test fixtures and goals. Goals, events, financial calculations, AI requests and decisions use the backend. No AI outcome is preselected.</p>
    <p className="small">Trigger Now uses the dataset's effective date ({day(bank.as_of)}). There is no future scheduler. Expenses enter as confirmed outstanding obligations, not posted transactions.</p>
    <button className="outline-btn" disabled={bank.busy} onClick={()=>bank.mutate('/scenario',{scenario:'integrated_demo',reset:true,fixture:bank.presenter_fixture || 'customer'},'New isolated presenter run created; previous records retained.','POST',false)}>New isolated run</button>
    <button className="outline-btn" disabled={bank.busy} onClick={()=>bank.mutate('/scenario',{scenario:'integrated_demo',reset:true,fixture:'stable_holiday'},'Existing stable holiday test fixture loaded into a fresh isolated sandbox.','POST',false)}>Load stable holiday test fixture</button>
    {stable && <p className="small">Existing backend holiday ledger: steady recorded income, protected savings and one goal. This replaces financial inputs only in this isolated run. For a modest event, enter AUD 100 before Trigger Now; no PASS or recommendation is guaranteed.</p>}
    {!selected ? <button className="primary-btn" disabled={bank.busy} onClick={()=>bank.mutate('/goals',{kind:'holiday',target:stable ? 6000 : 10000,allocated:stable ? 1200 : 0,deadline:stable ? '2027-05-31' : deadline,confirmed:true},'Goal persisted through the normal authorised API.')}>Create holiday goal through backend</button> : <>
      <label className="field">Affected goal<select value={selected.goal_id} disabled={bank.busy} onChange={e=>setGoal(e.target.value)}>{bank.goals.map(g=><option key={g.goal_id} value={g.goal_id}>{g.kind} · {money(g.target)}</option>)}</select></label>
      <p>Existing required contribution: {money(selected.required_monthly)} / month. {selected.contribution == null ? 'No approved contribution schedule recorded yet.' : `Approved schedule: ${money(selected.contribution)} ${selected.frequency}, pause ${selected.pause_months || 0} month(s).`}</p>
      <label className="field">Unexpected event<select value={kind} onChange={e=>setKind(e.target.value)}><option value="car_repair">Car repair</option><option value="unexpected_bill">Unexpected bill</option></select></label>
      <label className="field">Amount (AUD)<input type="number" min="1" max="100000" step="0.01" value={amount} onChange={e=>setAmount(e.target.value)} /></label>
      <button className="primary-btn" disabled={bank.busy || !Number.isFinite(Number(amount)) || Number(amount)<=0} onClick={async()=>{const cash=bank.financial.cashflow;const previous={projected:cash.runway.projected_after_known_pending,capacity:cash.savings_capacity.comfortable};if(await bank.mutate('/presenter/events',{goal_id:selected.goal_id,amount:Number(amount),kind,confirmed:true},'Outstanding obligation persisted. Backend financial context recalculated.'))setBefore(previous)}}>Trigger Now</button>
    </>}
    {before && <table className="demo-comparison"><caption>Actual backend financial impact</caption><thead><tr><th>Metric</th><th>Before event</th><th>After event</th></tr></thead><tbody><tr><th>Projected cash after obligations</th><td>{money(before.projected)}</td><td>{money(bank.financial.cashflow.runway.projected_after_known_pending)}</td></tr><tr><th>Historical comfortable capacity</th><td>{money(before.capacity)}</td><td>{money(bank.financial.cashflow.savings_capacity.comfortable)}</td></tr></tbody></table>}
    <button className="outline-btn" onClick={()=>setPanel(!panel)}>Notifications <span className="badge">{notifications.filter(n=>!read.includes(n.id)).length}</span></button>
    {panel && <section aria-label="Backend event notifications">{notifications.length===0 && <p>No presenter event has been recorded.</p>}{notifications.map(n=><button className="card pad demo-notification" key={n.id} onClick={()=>open(n)}><strong>{n.title}</strong><span>{n.message}</span><span className="small">Persisted {day(n.on)} · Open affected goal assessment →</span></button>)}</section>}
    {!!bank.demo_approval_history?.length && <section><h3>Persisted approval history</h3>{bank.demo_approval_history.map(h=><div className="card pad" key={h.draft_id}><strong>Approved by {h.by} · {h.calculation_ref}</strong><p>Contribution {money(h.before.contribution)} → {money(h.after.contribution)} {h.after.frequency}. Deadline {day(h.before.deadline)} → {day(h.after.deadline)}.</p></div>)}</section>}
    {!!bank.demo_approval_history?.length && <section><h3>Persisted approval history</h3>{bank.demo_approval_history.map(h=><article className="card pad" key={h.draft_id}><strong>Approved by {h.by} · {h.calculation_ref}</strong><p>Monthly/per-period contribution: {money(h.before.contribution)} → {money(h.after.contribution)} {h.after.frequency}.</p><p>Deadline: {day(h.before.deadline)} → {day(h.after.deadline)} · {h.after.pause_months || 0} month(s) pause.</p><p className="small">Original contribution is shown as unavailable when no schedule had been recorded.</p></article>)}</section>}
    <p className="small">AI assessment below uses the configured provider, authoritative Python tools, validation, independent checks and recovery. The normal manual-goal approval workflow remains below it; Only independently passing calculated schedules can be approved in this sandbox; production AI activation remains disabled.</p>
  </section>
}
