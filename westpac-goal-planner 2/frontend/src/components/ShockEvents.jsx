import { useState } from 'react'
import { useBank, money, day, label } from '../data/mockBank.js'
import { eventActions, eventTitle, eventNotice } from '../data/shockEvents.js'
import { Evidence } from './BankingPanels.jsx'

function EventCard({ event }) {
  const { mutate, busy, openTransactions } = useBank()
  const [remember, setRemember] = useState(false)
  const act = action => mutate(`/shocks/${event.event_ref}/action`, {
    action, evidence_version: event.evidence_version, confirmed: true,
    remember_expected: action === 'confirm_expected' && remember,
  }, 'Event feedback saved. Current financial-risk checks remain active.')
  return <article className={`card pad insight ${event.potential_disruption === 'high' ? 'critical' : 'advisory'}`}>
    <div className="row"><h3>{eventTitle(event)}</h3><span className="tag">{label(event.lifecycle_status)}</span></div>
    <p>{event.explanation}</p><p>{event.event_amount == null ? 'Projection / pattern' : money(event.event_amount)} · {day(event.date)}</p>
    <p className="small muted">{label(event.expectedness)} · Detection confidence: {event.confidence} · Potential cashflow impact: {event.potential_disruption}</p>
    <p className="small">{eventNotice(event)}</p>
    {event.reassessment_required && <p>New evidence is available. Please review this event again.</p>}
    {event.status === 'posted' && <label className="small"><input type="checkbox" checked={remember} onChange={e => setRemember(e.target.checked)} /> Remember comparable planned recurring or annual payments</label>}
    {event.lifecycle_status === 'new' ? <div className="actions">{eventActions.map(([action, title]) => <button key={action} className="small-btn" disabled={busy} onClick={() => act(action)}>{title}</button>)}</div>
      : <button className="small-btn" disabled={busy} onClick={() => act('reopen')}>Reopen for review</button>}
    {event.status === 'posted' && <button className="small-btn" onClick={openTransactions}>Review transaction categories in Accounts</button>}
    <details><summary>Financial context and evidence</summary>
      <Evidence values={{ current_balance: event.impact.current_balance, upcoming_obligations: event.impact.upcoming_obligations, projected_balance: event.impact.projected_balance, unreserved_projected_balance: event.impact.unreserved_projected_balance, comfortable: event.impact.comfortable }} />
      <p className="small muted">{event.uncertainty}</p><Evidence values={event.historical} />
      <p className="small muted">Methods: {event.detectors.map(label).join(', ')}. Statistical scores are not probabilities.</p>
    </details>
  </article>
}
export default function ShockEvents() {
  const { shocks, shock_history = [] } = useBank()
  const active = shocks.filter(e => e.notification_required)
  const reviewed = shocks.filter(e => !e.notification_required)
  return <><h2>Financial events</h2><p className="small muted">Events describe changes and uncertainty. They do not decide your goal strategy or confirm hardship.</p>
    {!active.length && <p>No new events need review.</p>}{active.map(e => <EventCard key={`${e.event_ref}-${e.evidence_version}`} event={e} />)}
    <details><summary>Reviewed and confirmed events ({reviewed.length})</summary>{reviewed.map(e => <EventCard key={`${e.event_ref}-${e.evidence_version}`} event={e} />)}</details>
    {!!shock_history.length && <details><summary>Event feedback history</summary>{shock_history.map(e => <div className="card pad" key={e.event_ref}><p>{label(e.status)} · {label(e.expectedness)} · {day(e.event_date)}</p>{e.history.map((a,i) => <p className="small muted" key={i}>{label(a.action)} · {day(a.on)}</p>)}</div>)}</details>}
  </>
}
