'use client';

import Panel from './Panel';

const PREPRINT = [
  { w: 'Drug X ',                          d: false },
  { w: 'cures ',                            d: true,  del: true },
  { w: 'treatment-resistant depression in ', d: false },
  { w: '87% ',                              d: true,  del: true },
  { w: 'of patients within ',               d: false },
  { w: '2 weeks ',                          d: true,  del: true },
  { w: 'of administration.',                d: false },
];

const PUBLISHED = [
  { w: 'Drug X ',                          d: false },
  { w: 'may alleviate ',                   d: true, add: true },
  { w: 'treatment-resistant depression in ', d: false },
  { w: '42% ',                             d: true, add: true },
  { w: 'of patients within ',              d: false },
  { w: '4 to 6 weeks ',                   d: true, add: true },
  { w: 'of administration.',               d: false },
];

interface DriftPanelProps {
  active: boolean;
}

export default function DriftPanel({ active }: DriftPanelProps) {
  return (
    <Panel label="FIG. 01 / CLAIM COMPARISON" labelRight="arxiv:2024.08211" style={{ background: 'var(--bk2)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
        {/* Preprint */}
        <div style={{ padding: '28px 28px 20px', borderRight: '1px solid var(--gr3)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--gr)' }}/>
            <span className="specimen">bioRxiv preprint / Mar 2026</span>
          </div>
          <p style={{ fontSize: 15, lineHeight: 1.9, fontWeight: 300, color: 'var(--wh2)' }}>
            {PREPRINT.map((t, i) => (
              <span key={i} style={{
                background:     t.del && active ? 'rgba(229,56,59,0.18)' : 'transparent',
                color:          t.del && active ? 'var(--rd)' : 'inherit',
                textDecorationLine: t.del && active ? 'line-through' : 'none',
                textDecorationColor: 'var(--rd)',
                padding:    t.d ? '1px 3px' : '0',
                borderRadius: 2,
                transition: `all 0.4s ease ${i * 0.09}s`,
              }}>{t.w}</span>
            ))}
          </p>
        </div>

        {/* Published */}
        <div style={{ padding: '28px 28px 20px', background: active ? 'rgba(245,197,24,0.03)' : 'transparent', transition: 'background 0.6s' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <div style={{
              width: 6, height: 6, borderRadius: '50%',
              background: active ? 'var(--rd)' : 'var(--gr)',
              transition: 'background 0.4s',
              boxShadow: active ? '0 0 8px var(--rd)' : 'none',
            }}/>
            <span className={`specimen ${active ? 'specimen-r' : ''}`} style={{ transition: 'color 0.4s' }}>
              Nature Medicine / peer reviewed / Aug 2026
            </span>
          </div>
          <p style={{ fontSize: 15, lineHeight: 1.9, fontWeight: 300, color: 'var(--wh2)' }}>
            {PUBLISHED.map((t, i) => (
              <span key={i} style={{
                background:  t.add && active ? 'rgba(245,197,24,0.2)' : 'transparent',
                color:       t.add && active ? 'var(--y)' : 'inherit',
                fontWeight:  t.add && active ? 600 : 300,
                padding:     t.d ? '1px 4px' : '0',
                borderRadius: 2,
                transition: `all 0.4s ease ${i * 0.1 + 0.2}s`,
              }}>{t.w}</span>
            ))}
          </p>
        </div>
      </div>

      {/* Deviation meter */}
      <div style={{
        borderTop: '1px solid var(--gr3)', padding: '14px 28px',
        display: 'flex', alignItems: 'center', gap: 16,
        background: active ? 'rgba(229,56,59,0.06)' : 'transparent',
        transition: 'background 0.5s',
      }}>
        <span className="specimen specimen-r">Semantic deviation score</span>
        <div style={{ flex: 1, height: 2, background: 'var(--gr3)', position: 'relative' }}>
          <div style={{
            position: 'absolute', left: 0, top: 0, height: '100%',
            background: 'linear-gradient(90deg, var(--y), var(--rd))',
            width: active ? '45%' : '0%',
            transition: 'width 1.8s cubic-bezier(0.4,0,0.2,1) 0.6s',
          }}/>
          {[0, 25, 50, 75, 100].map(p => (
            <div key={p} style={{ position: 'absolute', top: -4, left: `${p}%`, width: 1, height: 10, background: 'var(--gr3)' }}/>
          ))}
        </div>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 18, color: active ? 'var(--rd)' : 'var(--gr)', fontWeight: 700, transition: 'color 0.5s', minWidth: 48 }}>
          {active ? '45%' : '0%'}
        </div>
      </div>

      {/* Metadata footer */}
      <div style={{ borderTop: '1px solid var(--gr3)', padding: '10px 28px', display: 'flex', gap: 32 }}>
        {([
          ['Drift severity',   'HIGH',   'var(--rd)'],
          ['Papers affected',  '34',     'var(--y)'],
          ['Authors notified', '12',     'var(--grn)'],
          ['Detection latency','< 2.1s', 'var(--bl)'],
        ] as [string, string, string][]).map(([l, v, c]) => (
          <div key={l}>
            <div className="specimen" style={{ marginBottom: 2 }}>{l}</div>
            <div style={{ fontFamily: 'var(--mono)', fontSize: 13, color: c, fontWeight: 700 }}>{v}</div>
          </div>
        ))}
      </div>
    </Panel>
  );
}
