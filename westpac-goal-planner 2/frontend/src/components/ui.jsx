import Icon, { BrandMark } from './Icon.jsx'
import { money, useBank } from '../data/mockBank.js'

export const TABS = [
  { id: 'home', label: 'Home' },
  { id: 'accounts', label: 'Accounts', icon: 'coins' },
  { id: 'payments', label: 'Payments', icon: 'dollar' },
  { id: 'insights', label: 'Insights', icon: 'bulb' },
  { id: 'more', label: 'More', icon: 'grid' },
]

export function TabBar({ tab, onChange }) {
  return (
    <nav className="tabbar">
      {TABS.map(t => (
        <button key={t.id} className={'tab' + (tab === t.id ? ' active' : '')} onClick={() => onChange(t.id)}>
          {t.id === 'home' ? <BrandMark active={tab === 'home'} /> : <Icon name={t.icon} size={26} />}
          <span>{t.label}</span>
        </button>
      ))}
    </nav>
  )
}

export function TopIcons() {
  const { signOut } = useBank()
  return (
    <div className="topicons">
      <button aria-label="Search"><Icon name="search" /></button>
      <button aria-label="Contact us"><Icon name="headset" /></button>
      <button aria-label="Sign out" onClick={signOut}><Icon name="user" /></button>
    </div>
  )
}

export function PageHeader({ title, chips }) {
  return (
    <header className="pagehead">
      <TopIcons />
      <h1>{title}</h1>
      {chips && (
        <div className="chips">
          {chips.map((c, i) => (
            <button key={c.label} className={'chip' + (i === 0 ? ' active' : '')}>
              <Icon name={c.icon} size={20} color={i === 0 ? 'var(--red)' : 'currentColor'} />{c.label}
            </button>
          ))}
        </div>
      )}
    </header>
  )
}

export function SearchBar() {
  return (
    <div className="search">
      <Icon name="search" color="#575f65" /><span>Search</span><Icon name="mic" color="#575f65" />
    </div>
  )
}

export function SectionTitle({ children, link = true }) {
  return <h2 className="section-title">{children}{link && <Icon name="chevron" size={20} strokeWidth={2.400} />}</h2>
}

export function AccountCard({ a }) {
  return (
    <div className="card account">
      <div className="account-top">
        <div className="row"><span className="account-name">{a.name}</span><b className="account-bal">{money(a.balance)}</b></div>
                {a.label && <span className="muted">{a.label}</span>}
      </div>
      {a.cardEnding && (
        <>
          <div className="row line"><span className="row gap"><span className="minicard" />
            <span className="muted">Card ending in ...{a.cardEnding}</span></span><span className="link">View</span></div>
          <div className="row line"><span className="muted">Access more features</span><span className="link">Check list</span></div>
        </>
      )}
    </div>
  )
}

export function InProgress() {
  return (
    <div className="card progress-card">
      <Icon name="progress" color="var(--navy)" />
      <div>
        <h3>Set up your Westpac Choice</h3>
        <p className="muted">Just 2 steps to go</p>
        <button className="outline-btn">Share account details</button>
      </div>
      <Icon name="close" color="#575f65" />
    </div>
  )
}

export function Tile({ icon, label, onClick, badge }) {
  return (
    <button className="card tile" onClick={onClick}>
      <Icon name={icon} color="var(--navy)" />
      <span>{label}{badge && <em className="badge small">{badge}</em>}</span>
    </button>
  )
}

export function Bar({ label, value, pct, color }) {
  return (
    <div className="bar">
      <div className="row"><span className="muted">{label}</span><span style={{ color }}>{money(value)}</span></div>
      <div className="bar-track"><i style={{ width: pct + '%', background: color }} /></div>
    </div>
  )
}

export function MoneyTools({ onOpenGoals }) {
  const { cashFlow, categoriesTotal } = useBank()
  const max = Math.max(cashFlow.expenses, cashFlow.income, 1)
  return (
    <div className="grid2">
      <button className="card tool goal-tool" onClick={onOpenGoals}>
        <span className="eyebrow">Goal planner <em className="badge small">New</em></span>
        <Icon name="target" size={44} color="var(--navy)" strokeWidth={1.400} />
        <p className="muted">Set a savings goal and get a plan that adapts when life changes</p>
      </button>
      <div className="card tool">
        <span className="eyebrow">Cash flow</span>
        <b className="big">{money(cashFlow.income - cashFlow.expenses)}</b>
        <p className="muted small">Since {cashFlow.since} from<br />All accounts</p>
        <Bar label="Expenses" value={cashFlow.expenses} pct={cashFlow.expenses / max * 100} color="#991ad6" />
        <Bar label="Income" value={cashFlow.income} pct={cashFlow.income / max * 100} color="#181b25" />
      </div>
      <div className="card tool">
        <span className="eyebrow">Categories</span>
        <b className="big">{money(categoriesTotal)}</b>
        <p className="muted small">Since {cashFlow.since} from<br />All accounts</p>
      </div>
      <div className="card tool">
        <span className="eyebrow">Bills calendar</span>
        <Icon name="calendar" size={44} color="var(--navy)" strokeWidth={1.400} />
        <p className="muted">Add, track and review your bills</p>
      </div>
      <div className="card tool">
        <span className="eyebrow">Budget</span>
        <Icon name="tools" size={44} color="var(--navy)" strokeWidth={1.400} />
        <p className="muted">Track and plan your spending</p>
      </div>
    </div>
  )
}
