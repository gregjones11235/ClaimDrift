'use client';

import Panel from './Panel';

// Real drift event from the live `drift_events` index (event_id
// c3878f4b-0724-46dd-9e16-0b0a7beca1ea): a POTS whole-exome sequencing study
// whose top-line numbers were drastically moderated through peer review. The
// preprint claimed 55 significant genes / 92 pathogenic variants; the
// published version reports 15 genes / 16 variants. materiality_score = 0.9.
//   preprint:  10.1101/2024.05.03.24306814  (medRxiv, v1)
//   published: 10.1007/s10286-025-01110-2   (Clinical Autonomic Research)
const PREPRINT = [
  { w: 'WES revealed ',                     d: false },
  { w: '55 genes ',                         d: true,  del: true },
  { w: 'with genome-wide significance, harboring ', d: false },
  { w: '92 variants ',                      d: true,  del: true },
  { w: 'classified as pathogenic.',         d: false },
];

const PUBLISHED = [
  { w: 'WES identified ',                    d: false },
  { w: '16 rare variants ',                 d: true, add: true },
  { w: 'in ',                                d: false },
  { w: '15 genes ',                         d: true, add: true },
  { w: 'found in more than one case, predicted to be pathogenic.', d: false },
];

interface DriftPanelProps {
  active: boolean;
}

export default function DriftPanel({ active }: DriftPanelProps) {
  return (
    <Panel label="FIG. 01 / CLAIM COMPARISON" labelRight="medrxiv:2024.05.03.24306814" style={{ background: 'var(--bk2)' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr' }}>
        {/* Preprint */}
        <div style={{ padding: '28px 28px 20px', borderRight: '1px solid var(--gr3)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--gr)' }}/>
            <span className="specimen">medRxiv preprint / May 2024</span>
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
              Clin. Auton. Research / peer reviewed / 2025
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
            width: active ? '90%' : '0%',
            transition: 'width 1.8s cubic-bezier(0.4,0,0.2,1) 0.6s',
          }}/>
          {[0, 25, 50, 75, 100].map(p => (
            <div key={p} style={{ position: 'absolute', top: -4, left: `${p}%`, width: 1, height: 10, background: 'var(--gr3)' }}/>
          ))}
        </div>
        <div style={{ fontFamily: 'var(--mono)', fontSize: 18, color: active ? 'var(--rd)' : 'var(--gr)', fontWeight: 700, transition: 'color 0.5s', minWidth: 48 }}>
          {active ? '90%' : '0%'}
        </div>
      </div>

      {/* Metadata footer */}
      <div style={{ borderTop: '1px solid var(--gr3)', padding: '10px 28px', display: 'flex', gap: 32 }}>
        {([
          ['Materiality',      '0.90',   'var(--rd)'],
          ['Papers affected',  '2',      'var(--y)'],
          ['Authors notified', '2',      'var(--grn)'],
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
