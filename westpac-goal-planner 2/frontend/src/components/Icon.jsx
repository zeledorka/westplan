// Small stroke icon set drawn on a 24px grid. Add a name here to use it anywhere.
const P = {
  search: 'M10.5 17a6.5 6.5 0 1 1 0-13 6.5 6.5 0 0 1 0 13zM15.5 15.5 21 21',
  mic: 'M12 3a2.5 2.5 0 0 0-2.5 2.5v6a2.5 2.5 0 0 0 5 0v-6A2.5 2.5 0 0 0 12 3zM6.5 11.5a5.5 5.5 0 0 0 11 0M12 17v4M9 21h6',
  headset: 'M5 14v-2a7 7 0 0 1 14 0v2M5 13h2v5H5zM17 13h2v5h-2zM19 18c0 2-2 3-5 3',
  user: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20.5c.5-3.5 3.5-5.5 7.5-5.5s7 2 7.5 5.5z',
  chevron: 'M9 5l7 7-7 7',
  back: 'M15 5l-7 7 7 7',
  close: 'M6 6l12 12M18 6L6 18',
  gear: 'M12 15.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1',
  lock: 'M6 10h12v10H6zM8.5 10V7.5a3.5 3.5 0 0 1 7 0V10M9.5 15l2 2 3.5-3.5',
  progress: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5h5',
  coins: 'M12 9c4 0 7-1.3 7-3s-3-3-7-3-7 1.300-7 3 3 3 7 3zM5 6v4c0 1.700 3 3 7 3s7-1.300 7-3V6M5 10v4c0 1.700 3 3 7 3s7-1.300 7-3v-4M5 14v4c0 1.700 3 3 7 3s7-1.300 7-3v-4',
  dollar: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM14.500 9.500c-.4-1-1.300-1.500-2.500-1.500-1.500 0-2.500.8-2.500 2s1 1.700 2.500 2 2.500.8 2.500 2-1 2-2.500 2c-1.300 0-2.200-.6-2.600-1.600M12 6.500V8M12 16v1.500',
  bulb: 'M9 17h6M10 20.500h4M12 3a6 6 0 0 0-3.500 10.900c.5.400.8 1 .8 1.600V17h5.400v-1.500c0-.6.3-1.200.8-1.600A6 6 0 0 0 12 3z',
  grid: 'M4 4h6.500v6.500H4zM13.500 4H20v6.500h-6.500zM4 13.500h6.500V20H4zM13.500 13.500H20V20h-6.500z',
  paySomeone: 'M15 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM9.500 19c.4-3 2.500-5 5.500-5s5 2 5.500 5zM3 12h6M6.500 9l3 3-3 3',
  payid: 'M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM8.500 9v6M11.500 9h1.500a3 3 0 0 1 0 6h-1.500z',
  bpay: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM9.500 7.500h3a2 2 0 0 1 0 4h-3zM9.500 11.500H13a2.200 2.200 0 0 1 0 4.500H9.500zM9.500 7.500V16',
  globe: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM3 12h18M12 3c2.500 2.500 3.500 5.500 3.500 9s-1 6.500-3.500 9c-2.500-2.500-3.500-5.500-3.500-9s1-6.500 3.500-9z',
  atm: 'M4 4h16v5h-3v11H7V9H4zM7 9h10M12 11.500v6M10.500 13h2.200a1 1 0 0 1 0 2h-1.400a1 1 0 0 0 0 2h2.200',
  card: 'M3.500 6h17v12h-17zM3.500 10h17',
  dots: 'M6 12h.01M12 12h.01M18 12h.01',
  list: 'M9 7h11M9 12h11M9 17h11M4.500 7h.01M4.500 12h.01M4.500 17h.01',
  calendar: 'M4 6h16v14H4zM4 10h16M8 3.500V7M16 3.500V7M8 13.500h2M14 13.500h2M8 17h2',
  tools: 'M7 4v5.500a2 2 0 0 0 1 1.700V20h2v-8.800a2 2 0 0 0 1-1.700V4M14 4h5l1 3h-2v13h-2V7h-2z',
  bank: 'M3.500 9.500 12 4l8.500 5.500zM5.500 10v8M9.500 10v8M14.500 10v8M18.500 10v8M3.500 20h17',
  home: 'M4 11l8-7 8 7M6 9.500V20h12V9.500M10 20v-5.500h4V20',
  car: 'M5 16.500V12l2-5h10l2 5v4.500zM5 12h14M7 16.500v2M17 16.500v2M8 14.200h.01M16 14.200h.01',
  shield: 'M12 3l7.500 3v5.500c0 4.500-3 8-7.500 9.500-4.500-1.500-7.500-5-7.500-9.500V6zM9 12l2.200 2.200L15.200 10',
  chart: 'M4 20V10M9.500 20V5M15 20v-7M20 20V8M3 20h18',
  building: 'M4 20V5h9v15M13 9h7v11M7 8.500h3M7 12h3M7 15.500h3M16 12.500h1.500M16 16h1.500M3 20h18',
  nest: 'M4 12a8 8 0 0 0 16 0zM12 4.500a3 3.500 0 0 1 0 7 3 3.500 0 0 1 0-7z',
  calculator: 'M5 3.500h14v17H5zM8 7h8v3H8zM8.500 13.500h.01M12 13.500h.01M15.500 13.500h.01M8.500 17h.01M12 17h.01M15.500 17h.01',
  doc: 'M6 3.500h8l4 4V20.500H6zM14 3.500v4h4M9 12h6M9 15.500h6',
  sliders: 'M4 7h9M17 7h3M4 12h3M11 12h9M4 17h11M19 17h1M15 5v4M9 10v4M17 15v4',
  percent: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8.500 15.500l7-7M9 9h.01M15 15h.01',
  contact: 'M5 4h14v16H5zM12 12.500a2.200 2.200 0 1 0 0-4.400 2.200 2.200 0 0 0 0 4.400zM8 17c.5-1.700 2-2.500 4-2.500s3.500.8 4 2.500',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16.500a4.500 4.500 0 1 0 0-9 4.500 4.500 0 0 0 0 9zM12 12h.01',
  track: 'M4 20V4M4 20h16M8 16v-4M12 16V8M16 16v-6',
}

export default function Icon({ name, size = 24, color = 'currentColor', strokeWidth = 1.800, fill = 'none' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill={fill} stroke={color}
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name] || ''} />
    </svg>
  )
}

// Stand-in for the bank's logo mark in the tab bar. Replace with the official asset if you have permission.
export function BrandMark({ active }) {
  const style = active ? { fill: 'var(--red)', stroke: 'none' } : { fill: 'none', stroke: 'var(--navy)', strokeWidth: 1.300 }
  return (
    <svg width="34" height="24" viewBox="0 0 34 24" aria-hidden="true" {...style}>
      <path d="M1.500 6h8l3 12h-6.500z" /><path d="M13 6h8v12h-8z" /><path d="M32.500 6h-8l-3 12h6.500z" />
    </svg>
  )
}
