// Synthetic bank views now come exclusively from the local Python service.
import { createContext, useContext } from 'react'
export const BankContext = createContext(null)
export const useBank = () => useContext(BankContext)
export const money = n => n == null ? 'Not available' : new Intl.NumberFormat('en-AU', { style: 'currency', currency: 'AUD' }).format(n)
export const day = value => value ? new Date(`${value}T12:00:00`).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', year: 'numeric' }) : 'Not estimated'
export const label = value => String(value || '').replaceAll('_', ' ')
