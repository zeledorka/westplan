import { PageHeader, MoneyTools, Tile } from '../components/ui.jsx'

const CHIPS = [{ label: 'Overview', icon: 'progress' }, { label: 'Budget', icon: 'percent' }, { label: 'Bills', icon: 'calendar' }, { label: 'Track', icon: 'track' }]

export default function Insights({ onOpenGoals }) {
  return (
    <div className="page">
      <PageHeader title="Insights & tools" chips={CHIPS} />
      <div className="body grey">
        <div className="card promo">
          <div className="promo-art" />
          <div className="pad">
            <h3>Money Bootcamp</h3>
            <p className="muted">Be the boss of your money and unlock your potential with our money tools</p>
            <span className="link">Get started</span>
          </div>
        </div>
        <h2 className="section-title">Money tools</h2>
        <MoneyTools onOpenGoals={onOpenGoals} />
        <h2 className="section-title">More tools</h2>
        <div className="grid2">
          <Tile icon="target" label="Goal planner" badge="New" onClick={onOpenGoals} />
          <Tile icon="dollar" label="Net worth" />
          <Tile icon="calculator" label="Savings finder" />
          <Tile icon="percent" label="Tax organiser" />
        </div>
      </div>
    </div>
  )
}
