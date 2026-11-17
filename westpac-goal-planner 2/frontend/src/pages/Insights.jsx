import { PageHeader, Tile } from '../components/ui.jsx'
import ShockEvents from '../components/ShockEvents.jsx'
import { useBank } from '../data/mockBank.js'
import { FinancialFacts, InsightList } from '../components/BankingPanels.jsx'
export default function Insights({ onOpenGoals }) {
  const { openTool } = useBank()
  return <div className="page"><PageHeader title="Insights & tools" /><div className="body grey">
    <div className="grid2"><Tile icon="target" label="Goal planner" onClick={onOpenGoals} />{[['budgets', 'Budget Planner', 'percent'], ['bills', 'Bills Calendar', 'calendar'], ['savings', 'Savings Finder', 'calculator'], ['subscriptions', 'Subscriptions', 'list'], ['search', 'Transaction search', 'search'], ['spending', 'Spending breakdown', 'chart']].map(([name, title, icon]) => <Tile key={name} icon={icon} label={title} onClick={() => openTool(name)} />)}</div>
    <FinancialFacts /><ShockEvents /><h2>Personalised insights</h2><InsightList />
  </div></div>
}
