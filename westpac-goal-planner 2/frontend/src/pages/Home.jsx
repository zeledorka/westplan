import Icon from '../components/Icon.jsx'
import { SearchBar, SectionTitle, AccountCard, InProgress, MoneyTools } from '../components/ui.jsx'
import { FinancialFacts, InsightList } from '../components/BankingPanels.jsx'
import { useBank, money } from '../data/mockBank.js'

export default function Home({ onOpenGoals }) {
  const { firstName, accounts, netWorth, signOut, openTool } = useBank()
  const total = netWorth.assets + netWorth.liabilities || 1
  return (
    <div className="page home">
      <div className="hero">
        <div className="row">
          <span className="hero-hi">Hi {firstName}</span>
          <span className="row gap">
            <button className="hero-btn"><Icon name="headset" size={20} /> Contact us</button>
            <button className="hero-btn round" aria-label="Sign out" onClick={signOut}><Icon name="user" size={20} /></button>
          </span>
        </div>
        <p className="hero-q">How can we help you today?</p>
        <button onClick={() => openTool('search')} aria-label="Open transaction search"><SearchBar /></button>
      </div>
      <div className="sheet">
        <SectionTitle>Favourite accounts</SectionTitle>
        {accounts.filter(a => a.kind === 'cash').map(a => <AccountCard key={a.id} a={a} />)}
        <div className="row links">
          <span className="link row gap"><Icon name="lock" color="var(--red)" /> SafeBlock your accounts</span>
          <span className="link row gap">Edit favourites <Icon name="gear" color="var(--red)" /></span>
        </div>

        <SectionTitle link={false}>In progress</SectionTitle>
        <InProgress />

        <SectionTitle>Money tools</SectionTitle>
        <MoneyTools onOpenGoals={onOpenGoals} />

        <FinancialFacts />
        <SectionTitle link={false}>Personalised insights</SectionTitle><InsightList compact />
        <SectionTitle>Net worth</SectionTitle>
        <div className="card pad">
          <b className="big navy">{money(netWorth.net)}</b>
          <div className="networth-bar"><i style={{ width: netWorth.assets / total * 100 + '%' }} /></div>
          <div className="row"><span>{money(netWorth.assets)}<br />in assets</span>
            <span style={{ textAlign: 'right' }}>{money(netWorth.liabilities)}<br />in liabilities</span></div>
        </div>

        <div className="card progress-card">
          <Icon name="dollar" color="var(--navy)" />
          <div><h3>Payments list</h3><p className="muted">Upcoming, past, direct debits, PayTo agreements, BPAY View</p></div>
          <Icon name="chevron" color="var(--red)" strokeWidth={2.400} />
        </div>

        <button className="outline-btn center"><Icon name="gear" size={20} /> Customise home</button>
      </div>
    </div>
  )
}
