import type { ReactNode } from 'react';
import apex from './apex-eye.svg';
export function Logo() { return <span className="wordmark" translate="no">
<img src={apex} width="48" height="36" alt=""/>
<span>Torn<em>RED</em>eye</span>
</span>; }
export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const paths: Record<string, ReactNode> = {
    search: <>
<circle cx="10.5" cy="10.5" r="6.5"/>
<path d="m16 16 5 5"/>
</>,
    menu: <path d="M4 6h16M4 12h16M4 18h16"/>,
    close: <path d="m6 6 12 12M18 6 6 18"/>,
    star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9Z"/>,
    arrow: <path d="M5 19 19 5M8 5h11v11"/>,
    refresh: <>
<path d="M20 7a9 9 0 1 0 1 9M20 3v5h-5"/>
</>,
    timeline: <>
<path d="M4 3v18h17M7 15l4-5 4 3 6-8"/>
<circle cx="11" cy="10" r="1"/>
</>,
    countries: <>
<circle cx="12" cy="12" r="9"/>
<path d="M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18Z"/>
</>,
    items: <>
<path d="m12 3 9 5-9 5-9-5 9-5ZM3 8v9l9 5 9-5V8M12 13v9"/>
</>,
    info: <>
<circle cx="12" cy="12" r="9"/>
<path d="M12 11v6M12 7v.2"/>
</>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.items}</svg>;
}
export function ItemArt({ name }: { name: string }) {
  const pill = /xanax|morphine|tablet|insulin|ketamine|vicodin|drug/i.test(name);
  const flower = /flower|blossom|orchid|dahlia|peony|heather|crocus|edelweiss|violet|tribulus|cherry|ceibo|bird of paradise/i.test(name);
  return <span className={`item-art ${pill ? 'medicine' : ''}`} aria-hidden="true">{pill ? <svg viewBox="0 0 64 64">
<g transform="rotate(-30 32 32)">
<rect x="10" y="22" width="44" height="21" rx="10.5" fill="#f5e9d2"/>
<path d="M32 22h12a10.5 10.5 0 0 1 0 21H32Z" fill="#ccbda8"/>
<path d="M14 29c2-4 6-4 13-4" fill="none" stroke="#fff9ed" strokeWidth="2" strokeLinecap="round"/>
<path d="M32 23v19" stroke="#b6a493" strokeWidth="1"/>
</g>
</svg> : <svg viewBox="0 0 48 48" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">{flower ? <>
<path d="M24 29v15m0-8q-10-9-13-2 5 7 13 5"/>
<path d="M24 24C8 35 6 15 18 18 8 1 31 2 27 17 42 6 45 28 30 26 43 40 22 44 24 29 13 43 3 26 20 24Z"/>
</> : /plushie|neko/i.test(name) ? <>
<path d="m12 17-2-10 11 6q3-2 6 0l11-6-2 10q10 22-12 23Q2 39 12 17Z"/>
<path d="M17 24h1m12 0h1m-11 7q4 5 8 0"/>
</> : <>
<path d="m24 5 17 9-17 10L7 14 24 5ZM7 14v20l17 9 17-9V14M24 24v19"/>
</>}</svg>}</span>;
}
