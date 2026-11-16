import Icon from '../components/Icon.jsx'
import { PageHeader, AccountCard, InProgress, SectionTitle } from '../components/ui.jsx'
import { useBank, money } from '../data/mockBank.js'

export default function Accounts() {
  const { accounts, recent } = useBank()
  const loans = accounts.filter(a => a.kind === 'loan')
  const day = d => new Date(d + 'T00:00:00').toLocaleDateString('en-AU', { day: 'numeric', month: 'short' })
  return (
    <div className="page">
      <PageHeader title="Accounts" chips={[{ label: 'Overview', icon: 'coins' }, { label: 'Cards', icon: 'card' }]} />
      <div className="body grey">
        <h4 className="group">Cash</h4>
        {accounts.filter(a => a.kind === 'cash').map(a => <AccountCard key={a.id} a={a} />)}
        {loans.length > 0 && <h4 className="group">Loans</h4>}
        {loans.map(a => <AccountCard key={a.id} a={a} />)}
        <div className="row links end"><span className="link row gap">Edit accounts <Icon name="gear" color="var(--red)" /></span></div>
        <h4 className="group">Recent transactions</h4>
        <div className="card">
          {recent.map((t, i) => (
            <div className="row line tx" key={i}>
              <span><span className="tx-desc">{t.description}</span><span className="muted small">{day(t.date)}</span></span>
              <b className={t.amount > 0 ? 'credit' : ''}>{money(t.amount)}</b>
            </div>
          ))}
        </div>
        <div className="card pad row">
          <div><h3>Open a new account</h3><p className="muted">Bank accounts, credit cards, loans, business banking and more</p></div>
          <Icon name="doc" size={40} color="var(--navy)" strokeWidth={1.400} />
        </div>
        <SectionTitle link={false}>In progress</SectionTitle>
        <InProgress />
      </div>
    </div>
  )
}
