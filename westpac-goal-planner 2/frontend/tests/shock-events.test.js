import test from 'node:test'
import assert from 'node:assert/strict'
import { eventTitle, eventNotice, eventActions } from '../src/data/shockEvents.js'
test('income events are not described as paid expenses', () => {
  assert.equal(eventTitle({ event_type: 'missing_income' }), 'Expected income has not arrived')
  assert.match(eventNotice({ status: 'income_missing' }), /no additional expense/)
})
test('paid, outstanding and anticipated events remain distinct', () => {
  assert.match(eventNotice({ status: 'posted' }), /no second debit/)
  assert.match(eventNotice({ status: 'outstanding' }), /included once/)
  assert.match(eventNotice({ status: 'anticipated' }), /not deducted/)
})
test('event feedback actions never approve or alter goals', () => {
  assert.deepEqual(eventActions.map(a => a[0]), ['acknowledge','confirm_expected','confirm_unexpected','dismiss','resolve'])
  assert.ok(!eventActions.some(a => /approve|goal/.test(a[0])))
})
