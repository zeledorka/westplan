import { money } from '../data/mockBank.js'

export default function GoalAssessment({ assessment, historical = false }) {
  if (!assessment) return null
  const safety = assessment.safety_assessment || assessment
  const rows = safety.strategies || []
  const findings = [...(safety.findings || []), ...rows.flatMap(row => row.findings || [])]
  const calc = assessment.calculation
  const schedule = assessment.schedule
  return <section className="card pad" aria-label="Goal feasibility result" role="status">
    <h3>{historical ? 'Historical financial feasibility' : 'Financial feasibility'}: {safety.status}</h3>
    {assessment.assessed_at && <p className="small">Assessed {assessment.assessed_at}. {historical && 'Financial or goal facts have changed; this result does not certify current affordability.'}</p>}
    {schedule && <p>Required: {money(schedule.required_per_period)} {schedule.frequency}. Evaluated contribution: {money(schedule.contribution)} {schedule.frequency}. Completion: {schedule.completion_date || 'Not established'}.</p>}
    {schedule?.next_contribution_date && <p>Next contribution: {schedule.next_contribution_date}.</p>}
    {calc && <p>Combined commitments: {money(calc.aggregate_monthly_equivalent)} / month. Comfortable capacity: {money(calc.comfortable_capacity)} / month. Stretch capacity: {money(calc.stretch_capacity)} / month.</p>}
    {findings.map((finding, index) => <p key={`${finding.code}-${index}`}>{finding.message}</p>)}
    {safety.status !== 'PASS' && <p>This goal has not been saved or approved. Try reducing the target, extending the deadline, or resolving the reported obligations or missing evidence.</p>}
    {calc?.dated_cashflow?.available && <p>Dated projection through {calc.dated_cashflow.end}: minimum cash {money(calc.dated_cashflow.minimum_cash)} on {calc.dated_cashflow.minimum_date}. Income and ordinary spending include estimates.</p>}
    <p className="small muted">Conditional on the recorded evidence and assessment date; this is not a guarantee of future affordability.</p>
  </section>
}
