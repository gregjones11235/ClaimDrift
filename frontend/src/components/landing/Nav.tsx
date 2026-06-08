'use client';

import Logo from './Logo';

interface NavProps {
  scrollY: number;
}

// Single nav link to the on-page agent-pipeline section. (GitHub lives in the
// footer; the agent-activity dashboard is reachable from the hero CTA.)
const NAV_LINKS: { label: string; href: string; external?: boolean }[] = [
  { label: 'How It Works', href: '#how-it-works' },
];

export default function Nav({ scrollY }: NavProps) {
  const pinned = scrollY > 50;
  return (
    <nav style={{
      position: 'fixed', top: 0, left: 0, right: 0, zIndex: 500,
      display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      padding: '0 40px', height: 52,
      background: pinned ? 'rgba(10,10,10,0.95)' : 'transparent',
      backdropFilter: pinned ? 'blur(20px)' : 'none',
      borderBottom: pinned ? '1px solid var(--gr3)' : '1px solid transparent',
      transition: 'all 0.3s ease',
    }}>
      <a href="#" style={{ textDecorationLine: 'none', display: 'flex', alignItems: 'center' }}>
        <Logo variant="nav" />
      </a>

      <div style={{ display: 'flex', alignItems: 'center', gap: 32 }}>
        {NAV_LINKS.map(({ label, href, external }) => (
          <a
            key={label}
            href={href}
            {...(external ? { target: '_blank', rel: 'noopener' } : {})}
            data-h
            style={{
              fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--gr)',
              textDecorationLine: 'none', letterSpacing: '0.14em', textTransform: 'uppercase',
              transition: 'color 0.2s', borderBottom: '1px solid transparent', paddingBottom: 2,
            }}
            onMouseEnter={e => {
              (e.target as HTMLAnchorElement).style.color = 'var(--y)';
              (e.target as HTMLAnchorElement).style.borderBottomColor = 'var(--y)';
            }}
            onMouseLeave={e => {
              (e.target as HTMLAnchorElement).style.color = 'var(--gr)';
              (e.target as HTMLAnchorElement).style.borderBottomColor = 'transparent';
            }}
          >{label}</a>
        ))}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--grn)' }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--grn)', animation: 'pulse-ring 2s ease-out infinite' }}/>
        </div>
        <span className="specimen specimen-g">Monitoring active</span>
      </div>
    </nav>
  );
}
