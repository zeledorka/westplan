import { useEffect, useState } from 'react'
import { request } from '../api/client.js'
import { useBank, money, day, label } from '../data/mockBank.js'
import { Evidence } from '../components/BankingPanels.jsx'

function Budgets() {
  const { financial, categories, mutate, busy } = useBank()
  const [category, setCategory] = useState('Eating out')
  const [limit, setLimit] = useState('')
  const [protectedCategory, setProtected] = useState(false)
  async function submit(e) {
    e.preventDefault()
    await mutate('/budgets', { category, monthly_limit: Number(limit), protected: protectedCategory, confirmed: true }, 'Budget saved.', 'PUT')
  }
  return <><p>Budgets reflect spending this month. Remaining budget is not cash available for immediate savings.</p>
    <form className="card pad" onSubmit={submit}><h3>Set or edit a monthly budget</h3>
      <label className="field">Category<select value={category} onChange={e => setCategory(e.target.value)}>{categories.filter(c => !['Income', 'Transfers'].includes(c)).map(c => <option key={c}>{c}</option>)}</select></label>
      <label className="field">Monthly limit (AUD)<input required type="number" min="0" step="0.01" value={limit} onChange={e => setLimit(e.target.value)} /></label>
      <label><input type="checkbox" checked={protectedCategory} onChange={e => setProtected(e.target.checked)} /> Protect this category from AI reductions</label>
      <button className="primary-btn" disabled={busy}>Save budget</button>
    </form>
    {financial.budgets.categories.map(b => <div className="card pad" key={b.category}>
      <div className="row"><h3>{b.category}</h3><span className="tag">{label(b.status)}</span></div>
      <p>Spent {money(b.spent)} · {b.monthly_limit == null ? 'No budget selected' : `Limit ${money(b.monthly_limit)} · Remaining ${money(b.remaining)}`}</p>
      <p className="small muted">{label(b.source)} · Essential {money(b.essential_spent)} / discretionary {money(b.discretionary_spent)}{b.protected ? ' · Protected' : ''}</p>
      <button className="small-btn" disabled={busy} onClick={() => { setCategory(b.category); setLimit(b.monthly_limit ?? ''); setProtected(b.protected); window.scrollTo(0, 0) }}>Edit this budget</button>
    </div>)}
  </>
}
function Bills() {
  const { financial } = useBank()
  const calendar = financial.bills_calendar
  const upcoming = calendar.items.filter(b => !b.already_paid)
  const paid = calendar.items.filter(b => b.already_paid)
  const bill = b => <article className="card pad" key={b.bill_id}><div className="row"><h3>{b.merchant || b.category}</h3><strong>{money(b.expected_amount)}</strong></div>
    <p>{day(b.expected_date)} · {b.date_kind === 'estimated' ? 'Estimated date' : label(b.date_kind)}</p>
    <p className="muted small">{b.category} · {label(b.frequency)} · {b.essential ? 'Essential' : 'Discretionary'} · {b.confirmed ? 'Confirmed obligation' : 'Inferred / observed'} · {label(b.status)}</p></article>
  return <><p>Forecast dates are estimates unless marked confirmed. Paid expenses are listed separately.</p>
    {Object.entries(calendar.summary_days).map(([days, s]) => <div className="card pad" key={days}><h3>Next {days} days</h3><p>Expected {money(s.total_expected)}</p><p className="small muted">Confirmed {money(s.confirmed_due)} · Estimated {money(s.estimated_due)} · Anticipated {money(s.anticipated)}</p></div>)}
    <h2>Upcoming and outstanding</h2>{!upcoming.length && <p>No upcoming obligations detected.</p>}{upcoming.map(bill)}
    <details><summary>Latest paid recurring payments</summary>{paid.map(bill)}</details>
  </>
}
function Savings() {
  const { financial, mutate, busy } = useBank()
  return <><p>These are areas to review, not a goal strategy. Potential amounts are conditional, may overlap and are not guaranteed.</p>
    {!financial.savings_finder.length && <p>No eligible opportunities currently identified.</p>}
    {financial.savings_finder.map(o => <article className="card pad" key={o.opportunity_id}><h3>{o.category}: {label(o.kind)}</h3>
      <p>Potential monthly range: {money(o.monthly_savings_range.minimum)}–{money(o.monthly_savings_range.maximum)}</p>
      <p className="muted small">{o.confidence} confidence · User confirmation required · {o.savings_range_basis}</p><Evidence values={o.evidence} />
      <div className="actions"><button className="small-btn" disabled={busy} onClick={() => mutate('/feedback', { action: 'protect_category', category: o.category, confirmed: true }, 'Category protected.')}>Protect category</button>
        <button className="small-btn" disabled={busy} onClick={() => mutate('/feedback', { action: 'reject_adjustment', category: o.category, insight_id: o.opportunity_id, confirmed: true }, 'Preference saved; no budget was changed.')}>I do not want this reduction</button></div>
    </article>)}</>
}
function Subscriptions() {
  const { subscriptions, mutate, busy } = useBank()
  const action = (s, value) => mutate(`/subscriptions/${s.subscription_id}/action`, { action: value, confirmed: true }, 'Simulated subscription status updated.')
  return <><p>Simulation only. Requesting cancellation changes no forecast until the simulated cancellation is confirmed. No merchant is contacted.</p>
    {!subscriptions.length && <p>No eligible recurring subscriptions detected.</p>}
    {subscriptions.map(s => <article className="card pad" key={s.subscription_id}><h3>{s.merchant}</h3><p>{money(s.typical_amount)} about every {s.period_days} days</p><strong>{label(s.status)}</strong>
      {['active', 'cancellation_failed'].includes(s.status) && <button className="small-btn" disabled={busy} onClick={() => action(s, 'request_cancellation')}>Request simulated cancellation</button>}
      {s.status === 'cancellation_requested' && <div className="actions"><button className="small-btn" disabled={busy} onClick={() => action(s, 'confirm_cancelled')}>Confirm simulated cancellation</button><button className="small-btn" disabled={busy} onClick={() => action(s, 'confirm_failed')}>Simulate failure</button></div>}
    </article>)}</>
}
export function TransactionList({ rows }) {
  const { categories, mutate, busy } = useBank()
  return <>{rows.map(t => <article className="card pad" key={t.transaction_id}><div className="row"><h3>{t.description}</h3><strong>{money(t.amount)}</strong></div><p className="small muted">{day(t.date)} · {t.category} · {label(t.classification_source)} · {t.uncertain ? 'Uncertain classification' : `${Math.round(t.confidence * 100)}% confidence`}</p>
    <details><summary>Correct category</summary><form onSubmit={async e => { e.preventDefault(); const data = new FormData(e.currentTarget); await mutate(`/transactions/${t.transaction_id}/category`, { category: data.get('category'), essential: data.get('essential') === 'on', confirmed: true }, 'Category correction saved.') }}>
      <label className="field">Category<select name="category" defaultValue={t.category}>{categories.map(c => <option key={c}>{c}</option>)}</select></label>
      <label><input name="essential" type="checkbox" defaultChecked={t.essential !== false} /> Essential spending</label><button disabled={busy} className="small-btn">Save correction</button>
    </form></details></article>)}</>
}
function Search() {
  const { scope_version } = useBank()
  const [question, setQuestion] = useState('How much did I spend on groceries last month?')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(e) { e.preventDefault(); setBusy(true); setError(''); try { setResult(await request('/search', { method: 'POST', body: { question } })) } catch (e) { setError(e.message) } finally { setBusy(false) } }
  useEffect(() => { setResult(null) }, [scope_version])
  return <><p>Constrained, read-only search templates. This search does not use Gemini.</p><form className="card pad" onSubmit={submit}><label className="field">Question<input value={question} onChange={e => setQuestion(e.target.value)} required /></label><button className="primary-btn" disabled={busy}>{busy ? 'Searching…' : 'Search transactions'}</button></form>
    <details><summary>Supported questions</summary>{['How much did I spend on groceries last month?', 'How much did I spend on transport over the last three months?', 'Which subscriptions increased in price?', 'What were my largest expenses this year?'].map(q => <button className="small-btn" key={q} onClick={() => setQuestion(q)}>{q}</button>)}</details>
    {error && <p role="alert" className="error">{error}</p>}{result && <div className="card pad"><p>{day(result.start)}–{day(result.end)}</p>{result.total_spent != null && <strong>{money(result.total_spent)} across {result.transaction_count} transactions</strong>}{result.transactions?.map((r, i) => <p key={i}>{day(r.date)} · {r.category} · {money(r.amount)}</p>)}{result.changes?.map((r, i) => <p key={i}>{day(r.date)} · {money(r.previous)} → {money(r.current)}</p>)}{(result.transactions?.length === 0 || result.changes?.length === 0) && <p>No matching records.</p>}</div>}
  </>
}
export default function BankingTools({ name, onBack }) {
  const { financial } = useBank()
  const titles = { budgets: 'Budget Planner', bills: 'Bills Calendar', savings: 'Savings Finder', subscriptions: 'Subscriptions', search: 'Transaction search', spending: 'Spending breakdown' }
  return <div className="page"><header className="pagehead"><button className="backbtn" onClick={onBack}>← Back</button><h1>{titles[name]}</h1></header><div className="body grey">
    {name === 'budgets' ? <Budgets /> : name === 'bills' ? <Bills /> : name === 'savings' ? <Savings /> : name === 'subscriptions' ? <Subscriptions /> : name === 'search' ? <Search /> : financial.spending.categories.map(c => <div className="card pad" key={c.category}><h3>{c.category}</h3><p>Average / month {money(c.monthly_total)}</p><p>Discretionary portion {money(c.monthly_discretionary)}</p></div>)}
  </div></div>
}
