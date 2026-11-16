import { useEffect, useRef, useState } from 'react'
import { request } from './api/client.js'
import { BankContext, day } from './data/mockBank.js'
import { TabBar } from './components/ui.jsx'
import Home from './pages/Home.jsx'
import Accounts from './pages/Accounts.jsx'
import Payments from './pages/Payments.jsx'
import Insights from './pages/Insights.jsx'
import More from './pages/More.jsx'
import GoalPlanner from './pages/GoalPlanner.jsx'
import Login from './pages/Login.jsx'
import BankingTools from './pages/BankingTools.jsx'

export default function App() {
  const [bank, setBank] = useState(null)
  const [customers, setCustomers] = useState([])
  const [tab, setTab] = useState('home')
  const [tool, setTool] = useState(null)
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const sequence = useRef(0)
  async function refresh() {
    const seq = ++sequence.current
    setBusy(true); setError('')
    try {
      const people = await request('/customers')
      if (seq === sequence.current) setCustomers(people.customers)
      const data = await request('/overview')
      if (seq === sequence.current) setBank(data)
    }
    catch (e) { if (seq === sequence.current) { if (e.status === 401) setBank(null); else setError(e.message) } }
    finally { if (seq === sequence.current) setBusy(false) }
  }
  useEffect(() => {
    refresh()
    return () => { sequence.current++ }
  }, [])
  async function selectCustomer(id) {
    const seq = ++sequence.current
    setBusy(true); setError(''); setNotice('')
    try {
      await request('/session', { method: 'POST', body: { customer_id: id, demo_acknowledged: true } })
      const data = await request('/overview')
      if (seq === sequence.current) { setBank(data); setTool(null); setTab('home') }
    } catch (e) { if (seq === sequence.current) setError(e.message) }
    finally { if (seq === sequence.current) setBusy(false) }
  }
  async function mutate(path, body, message = 'Saved.', method = 'POST', includeRevision = true) {
    const seq = ++sequence.current
    setBusy(true); setError(''); setNotice('')
    try {
      const data = await request(path, { method, scope: bank.scope_version,
        body: { ...body, ...(includeRevision ? { expected_revision: bank.revision } : {}) } })
      if (seq === sequence.current) { setBank(data); setNotice(message) }
      return true
    } catch (e) {
      if (seq === sequence.current) {
        setError(e.message)
        if (e.status === 409) { try { const latest = await request('/overview'); if (seq === sequence.current) setBank(latest) } catch { /* Keep the original actionable error visible. */ } }
        if (e.status === 401) setBank(null)
      }
      return false
    } finally { if (seq === sequence.current) setBusy(false) }
  }
  async function signOut() {
    sequence.current++; setBusy(true)
    try { await request('/session', { method: 'DELETE' }); setBank(null); setTool(null); setNotice(''); setError('') }
    catch (e) { setError(e.message) }
    finally { setBusy(false) }
  }
  const go = t => { setTool(null); setTab(t) }
  const openGoals = () => setTool('goals')
  const openTool = name => { setNotice(''); setTool(name) }
  const status = <>{busy && <p className="status" role="status">Loading banking information…</p>}{error && <p className="status error" role="alert">{error} <button onClick={refresh}>Refresh</button></p>}{notice && <p className="status success" role="status">{notice}</p>}</>
  if (!bank) return <div className="phone"><main className="screen">{status}<Login customers={customers} busy={busy} onSelect={selectCustomer} /></main></div>
  const content = tool === 'goals' ? <GoalPlanner onBack={() => setTool(null)} />
    : tool ? <BankingTools name={tool} onBack={() => setTool(null)} />
    : tab === 'home' ? <Home onOpenGoals={openGoals} />
    : tab === 'accounts' ? <Accounts />
    : tab === 'payments' ? <Payments />
    : tab === 'insights' ? <Insights onOpenGoals={openGoals} /> : <More />
  return <BankContext.Provider value={{ ...bank, busy, mutate, refresh, signOut, openTool }}>
    <div className="phone">
      <div className="contextbar">
        <label>Mock customer<select aria-label="Mock customer" disabled={busy} value={bank.selected_customer} onChange={e => selectCustomer(e.target.value)}>
          {customers.map(c => <option key={c.id} value={c.id}>{c.firstName}</option>)}
        </select></label>
        <label>Demo Scenario Selector<select aria-label="Demo Scenario Selector" disabled={busy} value={bank.scenario}
          onChange={e => mutate('/scenario', { scenario: e.target.value }, 'Scenario loaded from the backend.', 'POST', false)}>
          <option value="customer">Selected customer's records</option><option value="normal">Normal holiday demo</option>
          <option value="vet_paid">Emergency vet bill paid</option><option value="vet_unpaid">Emergency vet bill unpaid</option>
        </select></label>
        <p className="small muted">Synthetic snapshot · {day(bank.as_of)}</p>
        {bank.scenario !== 'customer' && <p className="small">Separate demo sandbox · <button className="link" disabled={busy} onClick={() => mutate('/scenario', { scenario: 'normal', reset: true }, 'Demo reset to normal.', 'POST', false)}>Reset demo to normal</button></p>}
      </div>
      {status}
      <main className="screen" key={`${bank.selected_customer}-${bank.scenario}-${tool || tab}`}>{content}</main>
      <TabBar tab={tab} onChange={go} />
    </div>
  </BankContext.Provider>
}
