// Synthetic data only. This stands in for Layer 1 (mock banking data).
// mockUsers.json is written by backend/data/generate_mock_data.py.
// Swap these for calls to the FastAPI backend when it is ready.
import { createContext, useContext } from 'react'
import users from './mockUsers.json'

export { users }

// The signed-in user's summary: { firstName, accounts, cashFlow, categoriesTotal, netWorth, recent, ... }
export const BankContext = createContext(users[0])
export const useBank = () => useContext(BankContext)

// Demo sign-in only: plain-text check against the mock users. Never do this with real credentials.
export const signIn = (username, password) =>
  users.find(u => u.username === username.trim().toLowerCase() && u.password === password) || null

// Primary demo scenario from the architecture doc.
export const demoGoal = {
  name: 'Holiday',
  target: 6000,
  saved: 1200,
  monthsLeft: 8,
  requiredMonthly: 600,
  safeCapacity: 560,
}

export const money = n =>
  (n < 0 ? '-' : '') + '$' + Math.abs(Number(n)).toLocaleString('en-AU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
