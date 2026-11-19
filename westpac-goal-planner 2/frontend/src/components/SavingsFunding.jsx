import { useBank, money } from '../data/mockBank.js'

export default function SavingsFunding() {
  const bank = useBank()
  return <section className="card pad"><h2>Fund goals from existing savings</h2>
    <p>Designation earmarks funds. It does not transfer money or create a payment. Confirm your reserve and other commitments before making funds available.</p>
    {(bank.savings_funding || []).map(account => <form key={`${account.account_id}-${bank.revision}`} onSubmit={async event => {
      event.preventDefault()
      const form = new FormData(event.currentTarget)
      await bank.mutate('/savings-designations', { account_id: account.account_id,
        goal_available: Number(form.get('available')), protected: Number(form.get('reserve')),
        reserves_confirmed: form.get('confirmed') === 'on', confirmed: true,
        expected_financial_version: bank.state_version }, 'Savings designation recorded. Existing goals were reassessed; their schedules were not changed.')
    }}>
      <h3>{bank.accounts.find(a => a.id === account.account_id)?.name || 'Savings account'}</h3>
      <p>Savings balance: <strong>{money(account.balance)}</strong> · Current goal pool: {money(account.goal_available)}</p>
      <p>Current protected reserve: {account.reserves_confirmed || account.protected > 0 ? money(account.protected) : 'Not confirmed'}.</p>
      <p>Already allocated: {money(account.allocated_to_goals)} · Unallocated goal pool: {money(account.unallocated_goal_pool)} · Savings hold for unfunded obligations: {money(account.known_obligation_hold)}</p>
      <p>Other uncommitted balance: {account.reserves_confirmed ? money(account.uncommitted_balance) : 'Not established until reserve confirmation'}. This is not automatically available to goals.</p>
      <label className="field">Total amount available for goals (includes existing allocations)<input name="available" type="number" min="0" step="0.01" required defaultValue={account.goal_available} /></label>
      <label className="field">Protected reserve (enter explicitly)<input name="reserve" type="number" min="0" step="0.01" required defaultValue={account.reserves_confirmed || account.protected > 0 ? account.protected : ''} /></label>
      <label><input type="checkbox" name="confirmed" required /> I have reviewed the reserve and known obligations; the designated funds are available for goals.</label>
      <button className="primary-btn" disabled={bank.busy}>Confirm savings designation</button>
    </form>)}
    {!bank.savings_funding?.length && <p>No supported savings account with a known balance is available.</p>}
    {!!bank.designation_reassessments?.length && <p>Latest existing-goal reassessments: {bank.designation_reassessments.map(row => row.assessment.status).join(', ')}. Previous approvals are historical when their financial context changes.</p>}
  </section>
}
