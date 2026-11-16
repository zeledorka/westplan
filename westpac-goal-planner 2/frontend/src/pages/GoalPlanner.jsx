import Icon from '../components/Icon.jsx'
import { demoGoal, money } from '../data/mockBank.js'

// Placeholder for the team's feature. Mount GoalCard, RecommendationCard, ShockAlert,
// SafetyLog etc. here and replace demoGoal with the FastAPI responses.
export default function GoalPlanner({ onBack }) {
  const g = demoGoal, pct = g.saved / g.target * 100
  return (
    <div className="page">
      <header className="pagehead">
        <button className="backbtn" onClick={onBack}><Icon name="back" strokeWidth={2.400} /> Back</button>
        <h1>Goal planner</h1>
      </header>
      <div className="body grey">
        <div className="card pad">
          <div className="row"><h3>{g.name}</h3><span className="badge">Demo data</span></div>
          <b className="big navy">{money(g.saved)} <span className="muted of">of {money(g.target)}</span></b>
          <div className="networth-bar"><i style={{ width: pct + '%' }} /></div>
          <div className="row"><span>{money(g.requiredMonthly)}<br /><span className="muted">needed a month</span></span>
            <span style={{ textAlign: 'right' }}>{g.monthsLeft} months<br /><span className="muted">to go</span></span></div>
        </div>
        <div className="card pad">
          <h3>Safe savings capacity</h3>
          <p className="muted">You can safely put aside about {money(g.safeCapacity)} a month, {money(g.requiredMonthly - g.safeCapacity)} short of this goal.</p>
        </div>
        <div className="card pad placeholder">
          <h3>Recommendations</h3>
          <p className="muted">AI proposes. Safety validates. You decide. Recommendation cards with Approve, Reject and Snooze go here.</p>
        </div>
      </div>
    </div>
  )
}
