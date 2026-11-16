import { useEffect, useState } from 'react'
import { PageHeader, AccountCard } from '../components/ui.jsx'
import { useBank } from '../data/mockBank.js'
import { request } from '../api/client.js'
import { TransactionList } from './BankingTools.jsx'
export default function Accounts() {
  const { accounts, recent, scope_version, revision, openTool } = useBank()
  const [rows, setRows] = useState(recent)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('')
    request('/transactions?limit=200', { signal: controller.signal }).then(r => { if (r.scope_version === scope_version) setRows(r.transactions) }).catch(e => { if (e.name !== 'AbortError') setError(e.message) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [scope_version, revision])
  return <div className="page"><PageHeader title="Accounts" /><div className="body grey">
    {accounts.map(a => <AccountCard key={a.id} a={a} />)}
    <button className="outline-btn" onClick={() => openTool('search')}>Search transactions</button>
    <h2>Categorised transactions</h2>{loading && <p role="status">Loading transactions…</p>}{error && <p role="alert" className="error">{error}</p>}{!rows.length && !loading && <p>No transactions found.</p>}<TransactionList rows={rows} />
  </div></div>
}
