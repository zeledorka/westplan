import Icon from '../components/Icon.jsx'
import { PageHeader, SectionTitle, Tile } from '../components/ui.jsx'

// Ordered column by column: the grid fills two rows, scrolling sideways.
const PRODUCTS = [['coins', 'Bank accounts'], ['home', 'Home loans'], ['card', 'Credit cards'], ['car', 'Personal loans'],
  ['globe', 'International & travel'], ['shield', 'Insurance'], ['chart', 'Shares & investing'], ['nest', 'Superannuation'], ['building', 'Business products']]
const SERVICES = [['progress', 'Track service requests'], ['doc', 'Statements & documents'], ['sliders', 'Daily Pay Limit'],
  ['gear', 'Sign in settings'], ['shield', 'Security code and token'], ['contact', 'Contact']]
const POPULAR = [
  ['Westpac Rewards', 'Get access to offers, discounts and cashback'],
  ['Family Hub', "Manage your child's money, account services and controls"],
  ['Cost of living', 'Tools and strategies to help you navigate changing living costs'],
  ['Financial wellbeing hub', 'Explore financial education tools to master your money'],
  ['Staying secure', 'Find out how to stay protected with Westpac'],
]

export default function More() {
  return (
    <div className="page">
      <PageHeader title="More" />
      <div className="body">
        <SectionTitle>Products</SectionTitle>
        <div className="hscroll two-rows">{PRODUCTS.map(([i, l]) => <Tile key={l} icon={i} label={l} />)}</div>
        <div className="card progress-card slim"><Icon name="calculator" color="var(--navy)" /><h3>Calculators</h3></div>
        <SectionTitle>Services</SectionTitle>
        <div className="hscroll">{SERVICES.map(([i, l]) => <Tile key={l} icon={i} label={l} />)}</div>
        <SectionTitle link={false}>Popular areas</SectionTitle>
        {POPULAR.map(([t, d]) => (
          <div className="card pad row popular" key={t}>
            <div><h3>{t}</h3><p className="muted">{d}</p></div><span className="popular-art" />
          </div>
        ))}
      </div>
    </div>
  )
}
