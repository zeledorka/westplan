import { useState } from 'react'
import { BrandMark } from '../components/Icon.jsx'
import { users, signIn } from '../data/mockBank.js'

export default function Login({ onSignIn }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const submit = e => {
    e.preventDefault()
    const u = signIn(username, password)
    u ? onSignIn(u) : setError("Those details don't match a demo user. Tap one below to fill them in.")
  }
  return (
    <div className="page login">
      <div className="login-top"><BrandMark active /><h1>Sign in</h1><p className="muted">Demo app. Fictional customers only.</p></div>
      <form className="body" onSubmit={submit}>
        <label className="field">Username
          <input value={username} onChange={e => setUsername(e.target.value)} autoCapitalize="none" autoCorrect="off" /></label>
        <label className="field">Password
          <input type="password" value={password} onChange={e => setPassword(e.target.value)} /></label>
        {error && <p className="error">{error}</p>}
        <button className="primary-btn">Sign in</button>
        <h4 className="group">Demo users</h4>
        {users.map(u => (
          <button type="button" key={u.id} className="card pad demo-user"
            onClick={() => { setUsername(u.username); setPassword(u.password); setError('') }}>
            <b>{u.username}</b><span className="muted small">{u.persona}</span>
          </button>
        ))}
      </form>
    </div>
  )
}
