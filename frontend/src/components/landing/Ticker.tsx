'use client';

interface TickerProps {
  items: string[];
  bg?: string;
  fg?: string;
  speed?: number;
}

export default function Ticker({ items, bg = 'var(--y)', fg = 'var(--bk)', speed = 30 }: TickerProps) {
  const str = items.join('     /     ');
  const full = str + '     /     ' + str;
  return (
    <div style={{
      background: bg,
      overflow: 'hidden',
      padding: '10px 0',
      borderTop: '1px solid rgba(255,255,255,0.06)',
      borderBottom: '1px solid rgba(255,255,255,0.06)',
    }}>
      <div style={{
        display: 'inline-block',
        whiteSpace: 'nowrap',
        animation: `marquee ${speed}s linear infinite`,
        fontFamily: 'var(--mono)',
        fontSize: 10,
        color: fg,
        letterSpacing: '0.2em',
        textTransform: 'uppercase',
      }}>
        {full}
      </div>
    </div>
  );
}
