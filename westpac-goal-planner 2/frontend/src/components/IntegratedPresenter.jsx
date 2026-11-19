import { useState } from 'react'
import { useBank, money, day, label } from '../data/mockBank.js'

export default function IntegratedPresenter({ collapsed = false, onAssessment }) {
  const bank=useBank()
  const [goalId,setGoal]=useState('')
  const [amount,setAmount]=useState('1500')
  const [kind,setKind]=useState('car_repair')
  const [eventBusy,setEventBusy]=useState(false)
  const [eventNotice,setEventNotice]=useState('')
  const selected=bank.goals.find(g=>g.goal_id===goalId) || bank.goals[0]
  const notifications=bank.notifications || []
  async function trigger(event) {
    event.preventDefault()
    if (!selected || eventBusy) return
    setEventBusy(true);setEventNotice('')
    try {
      const ok=await bank.mutate('/presenter/events',{goal_id:selected.goal_id,amount:Number(amount),kind,confirmed:true},'Unexpected expense recorded. Starting an AI assessment of your goal.')
      if (ok) {
        setEventNotice('Expense recorded by the backend. AI assessment is starting automatically.')
        onAssessment?.({goalId:selected.goal_id,eventId:crypto.randomUUID(),question:`Review this goal after the newly recorded ${kind.replaceAll('_',' ')} expense of AUD ${Number(amount)}. Use the current backend financial facts and Python calculations. Maintaining the approved schedule is valid if it remains safe.`})
      }
    } finally { setEventBusy(false) }
  }
  const controls=<form onSubmit={trigger} className="goal-event-form">
    {bank.goals.length>1 && <label className="field">Affected goal<select value={selected?.goal_id || ''} disabled={bank.busy || eventBusy} onChange={e=>setGoal(e.target.value)}>{bank.goals.map(g=><option key={g.goal_id} value={g.goal_id}>{label(g.kind)} · {money(g.target)}</option>)}</select></label>}
    <label className="field">Unexpected expense<select value={kind} onChange={e=>setKind(e.target.value)}><option value="car_repair">Car repair</option><option value="unexpected_bill">Unexpected bill</option></select></label>
    <label className="field">Amount (AUD)<input type="number" required min="1" max="100000" step="0.01" value={amount} onChange={e=>setAmount(e.target.value)} /></label>
    <button className="primary-btn" disabled={bank.busy || eventBusy || !selected || !Number.isFinite(Number(amount)) || Number(amount)<=0}>{eventBusy ? 'Recording expense…' : 'Trigger expense & assess goal'}</button>
    <p className="small muted">Demo only · confirmed unpaid obligation as of {day(bank.as_of)}. No bank payment is made.</p>
  </form>
  if (collapsed) return <details className="card pad"><summary>{selected ? `${label(selected.kind)} goal · ${money(selected.contribution)} ${selected.frequency}` : 'Goal schedule'} · expense recorded</summary>
    {eventNotice && <p className="status success" role="status">{eventNotice}</p>}
    {selected && <p>Target {money(selected.target)} · deadline {day(selected.deadline)} · {money(selected.allocated)} allocated.</p>}
    {controls}
    <details><summary>Backend notifications and approval history</summary>{notifications.map(n=><p key={n.id}><strong>{n.title}</strong> {n.message}</p>)}{(bank.demo_approval_history || []).map(h=><p key={h.draft_id}>Approved {h.calculation_ref}: {money(h.after.contribution)} {h.after.frequency} · {day(h.after.deadline)}</p>)}</details>
  </details>
  return <section className="card pad" aria-label="Goal schedule and unexpected expense">
    <span className="badge">Isolated banking demo</span>
    {selected && <><h2>{label(selected.kind)} goal · {money(selected.target)}</h2>
      <p><strong>{selected.assessment_current ? 'Safety-assessed schedule' : 'Recorded schedule · reassessment required'}: {selected.contribution == null ? 'Not established' : `${money(selected.contribution)} ${selected.frequency}`}</strong><br /><span className="small">{money(selected.allocated)} allocated · deadline {day(selected.deadline)}{selected.pause_months ? ` · ${selected.pause_months} month pause` : ''}</span></p></>}
    {eventNotice && <p className="status success" role="status">{eventNotice}</p>}
    {collapsed ? <details><summary>Goal schedule and another expense</summary>{controls}</details> : controls}
    <details><summary>Notifications & demo controls{notifications.length ? ` (${notifications.length})` : ''}</summary>
      {notifications.map(n=><article className="card pad" key={n.id}><strong>{n.title}</strong><p>{n.message}</p><span className="small">{day(n.on)}</span></article>)}
      <button className="outline-btn" disabled={bank.busy} onClick={()=>bank.mutate('/scenario',{scenario:'integrated_demo',reset:true,fixture:bank.presenter_fixture || 'customer'},'New isolated run created; previous records retained.','POST',false)}>New isolated run</button>
      <button className="outline-btn" disabled={bank.busy} onClick={()=>bank.mutate('/scenario',{scenario:'integrated_demo',reset:true,fixture:'stable_holiday'},'Stable holiday fixture loaded in a fresh sandbox.','POST',false)}>Load stable holiday test fixture</button>
      {!!bank.demo_approval_history?.length && <section><h3>Approval history</h3>{bank.demo_approval_history.map(h=><p key={h.draft_id}>{h.calculation_ref}: {money(h.before.contribution)} → {money(h.after.contribution)} {h.after.frequency} · {day(h.after.deadline)}</p>)}</section>}
    </details>
  </section>
}
