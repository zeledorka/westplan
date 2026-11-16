import { PageHeader, Tile } from '../components/ui.jsx'

const ITEMS = [
  ['paySomeone', 'Pay someone'], ['payid', 'Pay to PayID'], ['bpay', 'BPAY'], ['globe', 'Pay overseas'],
  ['atm', 'Cardless Cash'], ['card', 'View digital card'], ['dots', 'More ways to pay'],
]

export default function Payments() {
  return (
    <div className="page">
      <PageHeader title="Payments" chips={[{ label: 'Pay', icon: 'dollar' }, { label: 'Manage', icon: 'list' }]} />
      <div className="body grey">
        <div className="grid2">{ITEMS.map(([icon, label]) => <Tile key={label} icon={icon} label={label} />)}</div>
      </div>
    </div>
  )
}
