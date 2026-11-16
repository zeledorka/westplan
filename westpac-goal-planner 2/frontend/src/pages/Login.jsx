import { BrandMark } from '../components/Icon.jsx'
export default function Login({ customers, busy, onSelect }) {
  return <div className="page login">
    <div className="login-top"><BrandMark active /><h1>Select a mock customer</h1><p className="muted">Synthetic data only. This demo selection is not production authentication.</p></div>
    <div className="body">{customers.map(c => <button key={c.id} disabled={busy} className="card pad demo-user" onClick={() => onSelect(c.id)}>
      <b>{c.firstName}</b><span className="muted small">{c.persona}</span>
    </button>)}{!customers.length && !busy && <p>No customers loaded. Start the local backend and refresh.</p>}</div>
  </div>
}
