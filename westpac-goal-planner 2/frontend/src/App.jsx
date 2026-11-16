import { useState } from 'react'
import { TabBar } from './components/ui.jsx'
import Home from './pages/Home.jsx'
import Accounts from './pages/Accounts.jsx'
import Payments from './pages/Payments.jsx'
import Insights from './pages/Insights.jsx'
import More from './pages/More.jsx'
import GoalPlanner from './pages/GoalPlanner.jsx'
import Login from './pages/Login.jsx'
import { BankContext } from './data/mockBank.js'

export default function App() {
  const [user, setUser] = useState(null) // the signed-in mock user
  const [tab, setTab] = useState('home')
  const [goals, setGoals] = useState(false) // true while the Goal planner screen is open
  const open = () => setGoals(true)
  const go = t => { setGoals(false); setTab(t) }
  if (!user) return <div className="phone"><main className="screen"><Login onSignIn={u => { setUser(u); go('home') }} /></main></div>
  const screen = goals ? <GoalPlanner onBack={() => setGoals(false)} />
    : tab === 'home' ? <Home onOpenGoals={open} />
    : tab === 'accounts' ? <Accounts />
    : tab === 'payments' ? <Payments />
    : tab === 'insights' ? <Insights onOpenGoals={open} />
    : <More />
  return (
    <BankContext.Provider value={{ ...user, signOut: () => setUser(null) }}>
    <div className="phone">
      <main className="screen" key={goals ? 'goals' : tab}>{screen}</main>
      <TabBar tab={tab} onChange={go} />
    </div>
    </BankContext.Provider>
  )
}
