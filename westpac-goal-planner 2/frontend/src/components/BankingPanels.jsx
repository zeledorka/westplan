import { useBank, money, day, label } from '../data/mockBank.js'

const monetaryFields = new Set(['monthly_average','recent_change','spent','monthly_limit','remaining','event_amount','balance','obligations','projected','buffer','observed_monthly_spend','monthly_discretionary','minimum_observed','monthly_std','typical_payment','previous_payment','current_payment'])
function evidenceValue(key, value) {
  if (value == null) return 'Unknown'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (monetaryFields.has(key)) return money(value)
  if (key.endsWith('_date')) return day(value)
  if (key.endsWith('_cv')) return new Intl.NumberFormat('en-AU', { style: 'percent', maximumFractionDigits: 1 }).format(value)
  return String(value)
}
export function Evidence({ values }) {
  return <dl className="evidence">{Object.entries(values || {}).map(([key, value]) => <div key={key}>
    <dt>{label(key)}</dt><dd>{evidenceValue(key, value)}</dd>
  </div>)}</dl>
}
export function InsightList({ compact = false }) {
  const { financial, mutate, busy } = useBank()
  const insights = financial.personalised_insights
  return <>{!insights.length && <p className="muted">No current personalised insights.</p>}{(compact ? insights.slice(0, 3) : insights).map(i => <article className={`card pad insight ${i.severity}`} key={i.insight_id}>
    <div className="row"><h3>{label(i.type)}</h3><span className="tag">{label(i.severity)}</span></div>
    <p>{i.explanation}</p><p className="muted small">{i.category} · {i.confidence} confidence · through {day(i.observation_period.end)}</p>
    {!compact && <Evidence values={i.values} />}
    <div className="actions">{[['useful', 'Useful'], ['not_useful', 'Not useful'], ['dismiss', 'Dismiss']].map(([action, text]) => <button className="small-btn" disabled={busy} key={action} onClick={() => mutate('/feedback', { action, insight_id: i.insight_id, confirmed: true }, 'Feedback saved. Critical risk notices remain visible.')}>{text}</button>)}</div>
    {i.severity === 'critical' && <p className="small muted">This risk notice stays visible when dismissed.</p>}
  </article>)}</>
}
export function FinancialFacts() {
  const { financial } = useBank()
  const c = financial.cashflow
  return <div className="card pad"><h3>Cashflow and savings estimates</h3>
    <div className="row"><span>Comfortable / month</span><strong data-testid="comfortable-capacity">{money(c.savings_capacity.comfortable)}</strong></div>
    <div className="row"><span>Stretch / month</span><strong>{money(c.savings_capacity.stretch)}</strong></div>
    <div className="row"><span>Average monthly surplus</span><strong>{money(c.average_surplus)}</strong></div>
    {c.runway && <div className="row"><span>Projected cash after known pending bills</span><strong data-testid="projected-cash">{money(c.runway.projected_after_known_pending ?? c.runway.projected_balance)}</strong></div>}
    <p className="small muted">Historical estimates, not guaranteed affordability. Upcoming bills may be inferred; ordinary future spending is not fully forecast.</p>
  </div>
}
