export const eventActions = [
  ['acknowledge', 'Acknowledge'], ['confirm_expected', 'This was planned'],
  ['confirm_unexpected', 'This was unexpected'], ['dismiss', 'Dismiss event'], ['resolve', 'Already dealt with'],
]
export function eventTitle(event) {
  return ({ missing_income: 'Expected income has not arrived', delayed_income: 'Income arrived late',
    reduced_income: 'Income was smaller than usual', outstanding_obligation: 'Outstanding obligation',
    anticipated_expense: 'Anticipated expense', sustained_spending_change: 'Spending pattern changed',
    recurring_cost_trend: 'Recurring costs gradually increased', liquidity_pressure: 'Cashflow pressure',
    income_volatility_change: 'Income became more variable', persistent_income_reduction: 'Income stayed lower',
    activity_pattern: 'Unusual purchase activity', limited_history_review: 'Expense needs clarification',
  })[event.event_type] || 'Unusual financial event'
}
export function eventNotice(event) {
  if (event.status === 'outstanding') return 'Outstanding: included once in projected obligations.'
  if (event.status === 'anticipated') return 'Anticipated only: not deducted as a confirmed payment.'
  if (event.status?.startsWith('income_')) return 'Income evidence: no additional expense or assumed replacement credit.'
  if (event.status === 'posted') return 'Already posted: included in balances; no second debit.'
  return 'Estimate only: this does not establish financial hardship.'
}
