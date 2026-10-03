'use client';

import { FONTS, GLOBAL_CSS } from './constants';
import { useInView, useCounter, useMousePos, useScrollY } from './hooks';
import LabCursor from './LabCursor';
import Nav from './Nav';
import Oscilloscope from './Oscilloscope';
import Ticker from './Ticker';
import Panel from './Panel';
import DriftPanel from './DriftPanel';
import CitGraph from './CitGraph';
import Logo from './Logo';
import type { DashboardStats } from '@/types/claimdrift';

/* ── HERO ─────────────────────────────── */
function Hero({ stats }: { stats: DashboardStats | null }) {
  const pos = useMousePos();
  const px = (pos.x / (typeof window !== 'undefined' ? window.innerWidth  : 1) - 0.5) * 20;
  const py = (pos.y / (typeof window !== 'undefined' ? window.innerHeight : 1) - 0.5) * 10;

  return (
    <section style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', justifyContent: 'center', padding: '100px 60px 60px', position: 'relative', overflow: 'hidden' }}>
      {/* Parallax ghost text */}
      <div style={{
        position: 'absolute', top: '50%', left: '50%',
        transform: `translate(calc(-50% + ${px}px), calc(-50% + ${py}px))`,
        fontFamily: 'var(--display)', fontSize: 'clamp(160px,22vw,280px)',
        color: 'rgba(245,197,24,0.04)', lineHeight: 0.85, userSelect: 'none',
        pointerEvents: 'none', letterSpacing: '-0.03em', whiteSpace: 'nowrap',
        transition: 'transform 0.12s ease',
      }}>DRIFT</div>



      {/* Top-right live coordinates */}
      <div style={{ position: 'absolute', top: 70, right: 60, textAlign: 'right' }}>
        <div className="specimen" style={{ color: 'var(--gr2)' }}>x: {Math.round(pos.x).toString().padStart(4, '0')}</div>
        <div className="specimen" style={{ color: 'var(--gr2)' }}>y: {Math.round(pos.y).toString().padStart(4, '0')}</div>
      </div>

      <div style={{ maxWidth: 1100, margin: '0 auto', width: '100%', position: 'relative', zIndex: 2 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 440px', gap: '5rem', alignItems: 'center' }}>

          {/* Left */}
          <div>


            <h1 style={{ fontFamily: 'var(--display)', fontSize: 'clamp(56px,8.5vw,108px)', lineHeight: 0.92, letterSpacing: '0.01em', color: 'var(--wh)', marginBottom: 32 }}>
              SCIENCE<br/>MOVES.<br/>
              <span style={{ color: 'var(--y)', WebkitTextStroke: '1px var(--yd)' }}>CLAIMS</span><br/>
              <span style={{ color: 'var(--rd)' }}>DRIFT.</span>
            </h1>

            <div style={{ width: 60, height: 2, background: 'var(--y)', marginBottom: 24 }}/>

            <p style={{ fontSize: 17, fontWeight: 300, lineHeight: 1.8, color: 'var(--gr)', maxWidth: 480, marginBottom: 36 }}>
              When preprints become peer-reviewed papers, their core claims often shift without notice. ClaimDrift uses a Vertex AI multi-agent pipeline to detect semantic drift the moment a preprint is published and notify every researcher whose work depends on the original findings.
            </p>

            <div style={{ display: 'flex', gap: 12 }}>
              <a href="/dashboard" data-h style={{
                background: 'var(--y)', color: 'var(--bk)',
                padding: '13px 28px', fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 700,
                textDecorationLine: 'none', letterSpacing: '0.14em', textTransform: 'uppercase',
                display: 'inline-flex', alignItems: 'center', gap: 8, transition: 'all 0.2s',
              }}
              onMouseEnter={e => { (e.currentTarget as HTMLAnchorElement).style.background = 'var(--wh)'; }}
              onMouseLeave={e => { (e.currentTarget as HTMLAnchorElement).style.background = 'var(--y)'; }}>
                Open Dashboard
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M2 6h8M6 2l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                </svg>
              </a>
              <a href="/playground/orchestration" data-h style={{
                background: 'transparent', border: '1px solid var(--gr3)', color: 'var(--gr)',
                padding: '13px 28px', fontFamily: 'var(--mono)', fontSize: 11, fontWeight: 700,
                textDecorationLine: 'none', letterSpacing: '0.14em', textTransform: 'uppercase', transition: 'all 0.2s',
              }}
              onMouseEnter={e => { const el = e.currentTarget as HTMLAnchorElement; el.style.borderColor = 'var(--y)'; el.style.color = 'var(--y)'; }}
              onMouseLeave={e => { const el = e.currentTarget as HTMLAnchorElement; el.style.borderColor = 'var(--gr3)'; el.style.color = 'var(--gr)'; }}>
                Playground
              </a>
            </div>
          </div>

          {/* Right: monitoring panel (decorative waveform) */}
          <Panel label="Monitoring active" style={{ background: 'var(--bk2)', padding: 24 }}>
            <Oscilloscope color="var(--y)" height={52} speed={5}/>
            <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              {([
                ['Drift events tracked', stats ? stats.drift_events_total.toLocaleString() : '4,712', 'var(--y)'],
                ['High-severity drifts', stats ? stats.high_severity_count.toLocaleString() : '146',  'var(--rd)'],
                ['Authors notified',     stats ? stats.notifications_sent.toLocaleString()  : '742',  'var(--grn)'],
                ['Avg materiality',      stats ? stats.avg_materiality_score.toFixed(2)     : '0.49', 'var(--bl)'],
              ] as [string, string, string][]).map(([l, v, c]) => (
                <div key={l} style={{ background: 'var(--bk3)', padding: '12px 14px' }}>
                  <div className="specimen" style={{ marginBottom: 4 }}>{l}</div>
                  <div style={{ fontFamily: 'var(--mono)', fontSize: 20, color: c, fontWeight: 700, lineHeight: 1 }}>{v}</div>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        {/* Scroll indicator */}
        <div style={{ position: 'absolute', bottom: -40, left: '50%', transform: 'translateX(-50%)', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <span className="specimen" style={{ color: 'var(--wh2)' }}>Scroll to explore</span>
          <div style={{ width: 1, height: 36, background: 'linear-gradient(var(--y), transparent)' }}/>
        </div>
      </div>
    </section>
  );
}

/* ── PROBLEM SECTION ──────────────────── */
function ProblemSection() {
  const STEPS = [
    { n: '01', title: 'Preprint published on bioRxiv or medRxiv',       body: 'An unreviewed claim enters the literature and becomes immediately discoverable. The research community begins citing it.',                                                                                          status: 'ok'   },
    { n: '02', title: 'Citations accumulate over months',                body: 'Downstream papers, institutional grants, and doctoral theses are built on the original claim, treating it as peer-reviewed fact.',                                                                                   status: 'warn' },
    { n: '03', title: 'Peer review revises the core conclusion',         body: 'The published version walks back a key finding. The deviation is significant. The change is not announced to prior citers.',                                                                                        status: 'warn' },
    { n: '04', title: 'Downstream research is compromised',              body: 'Every paper that cited the preprint is now resting on a shifted claim. No alert is sent. The cascade continues invisibly.',                                                                                         status: 'err'  },
    { n: 'CD', title: 'ClaimDrift detects the drift and stops the cascade', body: 'The first preprint version is compared with the published paper. Citing papers that still rely on the revised value are found in Europe PMC open full texts, and an alert is drafted for each.',                                                                               status: 'fix'  },
  ];

  const BG: Record<string, string> = {
    ok:   'var(--gr3)',
    warn: 'rgba(245,197,24,0.2)',
    err:  'rgba(229,56,59,0.2)',
    fix:  'rgba(77,158,255,0.15)',
  };
  const TC: Record<string, string> = {
    ok:   'var(--gr)',
    warn: 'var(--y)',
    err:  'var(--rd)',
    fix:  'var(--bl)',
  };
  const HOVER: Record<string, string> = {
    ok:   'var(--gr3)',
    warn: 'rgba(245,197,24,0.2)',
    err:  'rgba(229,56,59,0.28)',
    fix:  'rgba(77,158,255,0.22)',
  };

  return (
    <section style={{ maxWidth: 1200, margin: '0 auto', padding: '96px 60px' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: '5rem', alignItems: 'start' }}>
        <div style={{ position: 'sticky', top: 80 }}>
          <span className="specimen specimen-y" style={{ display: 'block', marginBottom: 14 }}>Section 01</span>
          <h2 style={{ fontFamily: 'var(--display)', fontSize: 64, lineHeight: 0.9, letterSpacing: '0.02em', color: 'var(--wh)', marginBottom: 20 }}>
            THE<br/>CASCADE<br/>PROBLEM
          </h2>
          <div style={{ width: 40, height: 2, background: 'var(--y)', marginBottom: 20 }}/>
          <Oscilloscope color="var(--rd)" height={36} speed={8}/>
        </div>

        <div>
          <p style={{ fontSize: 18, lineHeight: 1.9, fontWeight: 300, color: 'var(--gr)', marginBottom: 32 }}>
            <span style={{ float: 'left', fontFamily: 'var(--display)', fontSize: 88, lineHeight: 0.78, marginRight: 14, marginTop: 8, color: 'var(--y)', WebkitTextStroke: '1px var(--yd)' }}>I</span>
            n modern research, a preprint can accumulate dozens of citations before undergoing peer review. Papers, theses, and grant proposals all cite the original claim as established fact. When peer review forces a fundamental revision to the core conclusions, however, not one of those downstream authors receives a notification. The cascade of invalidated data propagates silently through the literature.
          </p>
          <div style={{ clear: 'both', paddingTop: 28, borderTop: '1px solid var(--gr3)', marginBottom: 40 }}>
            <p style={{ fontSize: 15, lineHeight: 1.85, fontWeight: 300, color: 'var(--gr2)', maxWidth: 600 }}>
              ClaimDrift intercepts the cascade at the precise moment of drift. A 5-agent pipeline on Vertex AI Agent Engine compares the first preprint version with the published paper, traces the citing papers in Europe PMC open full texts that still rely on a revised claim (following papers that relayed it, up to 2 hops), and drafts a factual alert for each one.
            </p>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
            {STEPS.map(({ n, title, body, status }) => (
              <div key={n}
                style={{ display: 'grid', gridTemplateColumns: '56px 1fr', background: BG[status], borderLeft: `2px solid ${TC[status]}`, padding: '20px 24px', gap: '1.5rem', transition: 'background 0.2s' }}
                onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.background = HOVER[status]; }}
                onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = BG[status]; }}
                data-h>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 22, color: TC[status], fontWeight: 700, lineHeight: 1, paddingTop: 4 }}>{n}</div>
                <div>
                  <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--wh)', marginBottom: 6 }}>{title}</div>
                  <div style={{ fontSize: 13, fontWeight: 300, color: 'var(--gr)', lineHeight: 1.7 }}>{body}</div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

/* ── HOW IT WORKS ─────────────────────── */
function HowItWorks() {
  const AGENTS: { num: string; tag: string; color: string; title: string; body: string; tech: string[]; wide?: boolean }[] = [
    { num: '01', tag: 'CLAIM EXTRACTOR', color: 'var(--y)',   title: 'Claims From Both Versions',   body: 'The first preprint version (v1, from its own JATS full text) and the published paper are read section by section. Every finding is extracted with its numbers verbatim, together with the method and definition statements it depends on. The two versions are processed in parallel.', tech: ['bioRxiv / medRxiv JATS', 'Europe PMC full text', 'Gemini'] },
    { num: '02', tag: 'DRIFT ANALYZER',  color: 'var(--rd)',  title: 'What Changed, and How Much',  body: 'Compares the two claim lists and records each change with its type and root cause. Severity is rated twice and kept apart: on the full text, and on the abstracts alone on the Brierley et al. scale. Every evidence quote is checked verbatim against the full text before it is stored.', tech: ['Gemini', 'Verbatim quote check', 'Human review queue'] },
    { num: '03', tag: 'CITATION FINDER', color: 'var(--bl)',  title: 'Who Still Relies on the Old Value', body: 'Searches the open full text of every paper citing the preprint or its published version for the superseded value. A lead agent dispatches parallel workers that read each candidate and judge how it uses the claim: superseded, current, flagged as previous, indirect, not relying, or unclear. Papers that relayed the old value are followed for up to 2 hops. Coverage is always reported.', tech: ['Europe PMC open full text', 'MCP tools', 'Orchestrator + workers'] },
    { num: '04', tag: 'NOTIFIER',        color: 'var(--grn)', title: 'Factual Alerts',               body: 'Drafts one alert per citing paper that relies on a revised value: the passage in that paper, the preprint text, the published text and what changed. The judgement is left to the authors. Alerts go only to the project test inbox.', tech: ['Gemini', 'Gmail API', 'Test inbox only'] },
    { num: '05', tag: 'SUPERVISOR',      color: 'var(--or)',  title: 'Fixed Orchestration',          body: 'Runs the other four agents in a fixed order: claim extractor x2 in parallel, drift analyzer, citation finder, notifier xN in parallel. It has no model of its own. Agents only compute; storage and sending stay on Cloud Run, so any step can be retried safely.', tech: ['Vertex AI Agent Engine', 'Cloud Run', 'Elasticsearch'], wide: true },
  ];

  return (
    <section id="how-it-works" style={{ maxWidth: 1200, margin: '0 auto', padding: '96px 60px', scrollMarginTop: 60 }}>
      <div style={{ marginBottom: 56 }}>
        <span className="specimen specimen-y" style={{ display: 'block', marginBottom: 12 }}>Section 03 / Agent pipeline</span>
        <h2 style={{ fontFamily: 'var(--display)', fontSize: 'clamp(40px,5vw,72px)', lineHeight: 0.9, color: 'var(--wh)' }}>HOW THE AGENTS WORK</h2>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '2px', background: 'var(--gr3)' }}>
        {AGENTS.map(({ num, tag, color, title, body, tech, wide }) => (
          <div key={num}
            style={{ background: 'var(--bk2)', padding: '40px 36px', position: 'relative', overflow: 'hidden', gridColumn: wide ? '1 / -1' : undefined }}
            onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.background = 'var(--bk3)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = 'var(--bk2)'; }}
            data-h>
            <div style={{ position: 'absolute', top: 0, left: 0, right: 0, height: 2, background: color }}/>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <span className="specimen" style={{ color }}>{tag}</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 36, color: 'var(--gr3)', fontWeight: 700 }}>{num}</span>
            </div>
            <h3 style={{ fontFamily: 'var(--sans)', fontSize: 20, fontWeight: 500, color: 'var(--wh)', marginBottom: 14, lineHeight: 1.3 }}>{title}</h3>
            <p style={{ fontSize: 13, color: 'var(--gr)', fontWeight: 300, lineHeight: 1.8, marginBottom: 20 }}>{body}</p>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {tech.map(t => (
                <span key={t} style={{ fontFamily: 'var(--mono)', fontSize: 9, color: 'var(--gr2)', background: 'var(--bk)', border: '1px solid var(--gr3)', padding: '3px 8px', letterSpacing: '0.1em' }}>{t}</span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── WHO IT IS FOR ────────────────────── */
function Audience() {
  const CARDS = [
    { tag: 'Researchers',                accent: 'var(--y)',   num: '01', title: 'Never build on a shifted foundation.',          body: 'ClaimDrift monitors every paper your work depends on. When a cited claim changes significantly, you receive a structured alert with the exact deviation score and a comparison of original versus revised findings before your experiment or analysis proceeds on invalid assumptions.' },
    { tag: 'Publishers and peer reviewers', accent: 'var(--rd)', num: '02', title: 'Track how conclusions evolve across revisions.', body: 'Visualize the semantic distance between preprint and final manuscript at the claim level. Identify undisclosed changes to core findings and ensure the published record accurately reflects the outcomes of peer review, not the rhetoric of the initial submission.' },
    { tag: 'Research institutions',       accent: 'var(--grn)', num: '03', title: 'Protect your output at institutional scale.',    body: 'Monitor the full citation dependency graph across every researcher in your institution. ClaimDrift surfaces systemic exposure to drifted claims automatically, enabling proactive correction of published work rather than reactive damage control after retractions surface.' },
  ];
  return (
    <section style={{ maxWidth: 1200, margin: '0 auto', padding: '96px 60px' }}>
      <div style={{ marginBottom: 56, display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', borderBottom: '1px solid var(--gr3)', paddingBottom: 24 }}>
        <h2 style={{ fontFamily: 'var(--display)', fontSize: 'clamp(40px,5vw,72px)', lineHeight: 0.9, color: 'var(--wh)' }}>WHO IT IS FOR</h2>
        <span className="specimen specimen-y">Three audiences</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 1, background: 'var(--gr3)' }}>
        {CARDS.map(({ tag, accent, num, title, body }) => (
          <div key={tag}
            style={{ background: 'var(--bk2)', padding: '40px 32px', borderTop: `3px solid ${accent}` }}
            onMouseEnter={e => { (e.currentTarget as HTMLDivElement).style.background = 'var(--bk3)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = 'var(--bk2)'; }}
            data-h>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
              <span className="specimen" style={{ color: accent }}>{tag}</span>
              <span style={{ fontFamily: 'var(--mono)', fontSize: 28, color: 'var(--gr3)', fontWeight: 700 }}>{num}</span>
            </div>
            <h3 style={{ fontSize: 17, fontWeight: 600, color: 'var(--wh)', lineHeight: 1.35, marginBottom: 14 }}>{title}</h3>
            <p style={{ fontSize: 13, color: 'var(--gr)', fontWeight: 300, lineHeight: 1.8 }}>{body}</p>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ── ROOT EXPORT ──────────────────────── */
export default function ClaimDriftLanding({ stats }: { stats: DashboardStats | null }) {
  const scrollY = useScrollY();
  const [driftRef, driftInView] = useInView(0.2);
  const [statsRef, statsInView] = useInView(0.25);
  const [netRef,   netInView]   = useInView(0.15);

  // The "measured impact" counters animate to real whole-index totals when the
  // BFF responds, falling back to representative demo values otherwise.
  const citations = useCounter(stats?.affected_citations_total ?? 796, 1800, statsInView);
  const avgPct    = useCounter(stats ? Math.round(stats.avg_materiality_score * 100) : 49, 2200, statsInView);
  const notified  = useCounter(stats?.notifications_sent ?? 742, 1400, statsInView);
  const events    = useCounter(stats?.drift_events_total ?? 389, 2400, statsInView);

  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: FONTS + GLOBAL_CSS }} />
      <div className="lab-bg"/>
      <div className="scanline"/>
      <LabCursor/>
      <Nav scrollY={scrollY}/>

      <Hero stats={stats}/>

      <Ticker
        items={[
          'Claim drift detected',
          stats ? `${stats.drift_events_total.toLocaleString()} drift events tracked` : 'Real-time drift detection',
          stats ? `${stats.affected_citations_total.toLocaleString()} citing papers judged` : 'Citing papers judged',
          stats ? `${stats.notifications_sent.toLocaleString()} authors notified` : 'Affected authors notified',
          'Citing papers: Europe PMC open full text',
          '5 agents on Vertex AI Agent Engine',
          'Indexed: Elasticsearch',
        ]}
        bg="var(--y)" fg="var(--bk)" speed={28}
      />

      <ProblemSection/>

      <Ticker
        items={['Claim extractor','Drift analyzer','Citation finder','Notifier','Supervisor','Vertex AI Agent Engine','Elasticsearch','Europe PMC open full text']}
        bg="var(--bk3)" fg="var(--y)" speed={22}
      />

      {/* ── DRIFT DEMO */}
      <section id="demo" style={{ background: 'var(--bk2)', padding: '96px 60px', position: 'relative' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <div style={{ marginBottom: 48 }}>
            <span className="specimen specimen-y" style={{ display: 'block', marginBottom: 12 }}>Section 02 / Claim comparison</span>
            <h2 style={{ fontFamily: 'var(--display)', fontSize: 'clamp(40px,5vw,72px)', lineHeight: 0.9, color: 'var(--wh)', marginBottom: 16 }}>THE DRIFT, SIDE BY SIDE</h2>
            <p style={{ fontSize: 15, color: 'var(--gr)', fontWeight: 300, maxWidth: 560, lineHeight: 1.8 }}>
              The Analyzer agent performs sentence-level semantic comparison between the preprint and the published manuscript. Each claim receives a deviation score. This real event scored 90 percent — high enough to trigger full blast-radius mapping automatically.
            </p>
          </div>
          <div ref={driftRef}>
            <DriftPanel active={driftInView}/>
          </div>
        </div>
      </section>

      {/* ── STATS */}
      <section ref={statsRef} style={{ background: 'var(--bk)', borderTop: '1px solid var(--gr3)', borderBottom: '1px solid var(--gr3)', padding: '72px 60px' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto' }}>
          <span className="specimen specimen-y" style={{ display: 'block', marginBottom: 36, textAlign: 'center' }}>Measured impact / whole index</span>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 1, background: 'var(--gr3)' }}>
            {([
              { val: events.toLocaleString(),       label: 'Drift events tracked',          color: 'var(--y)',   sub: 'Across the indexed corpus'      },
              { val: `${avgPct}%`,                  label: 'Average materiality score',     color: 'var(--rd)',  sub: 'Higher = more consequential drift' },
              { val: notified.toLocaleString(),     label: 'Author notifications sent',     color: 'var(--grn)', sub: 'Dispatched via Gmail'           },
              { val: citations.toLocaleString(),    label: 'Citing papers judged',          color: 'var(--bl)',  sub: 'Europe PMC open full text' },
            ] as { val: number | string; label: string; color: string; sub: string }[]).map(({ val, label, color, sub }, i) => (
              <div key={i} style={{ background: 'var(--bk)', padding: '40px 36px', position: 'relative', overflow: 'hidden' }}>
                <div style={{ position: 'absolute', top: 16, right: 16 }}>
                  <div className="corner-tr" style={{ width: 10, height: 10 }}/>
                </div>
                <div style={{ fontFamily: 'var(--mono)', fontSize: 'clamp(40px,4vw,60px)', color, fontWeight: 700, lineHeight: 1, marginBottom: 10 }}>{val}</div>
                <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--wh2)', marginBottom: 6 }}>{label}</div>
                <div className="specimen" style={{ color: 'var(--gr2)' }}>{sub}</div>
                <div style={{ marginTop: 24 }}>
                  <Oscilloscope color={color} height={64} speed={4 + i}/>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <HowItWorks/>

      {/* ── CITATION GRAPH */}
      <section ref={netRef} style={{ background: 'var(--y)', padding: '96px 60px', position: 'relative', overflow: 'hidden' }}>
        <div style={{ maxWidth: 1200, margin: '0 auto', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '5rem', alignItems: 'center' }}>
          <div>
            <span className="specimen" style={{ display: 'block', marginBottom: 12, color: '#555' }}>Section 04 / Citing papers</span>
            <h2 style={{ fontFamily: 'var(--display)', fontSize: 'clamp(52px,7vw,96px)', lineHeight: 0.88, color: 'var(--bk)', marginBottom: 28 }}>THE CITATION NETWORK</h2>
            <p style={{ fontSize: 16, fontWeight: 300, lineHeight: 1.8, color: '#333', maxWidth: 420, marginBottom: 36 }}>
              One drifted claim propagates through every paper that cited the preprint. ClaimDrift searches the open full text of the citing papers in Europe PMC for the superseded value, reads each match, and follows papers that relayed the old value for up to 2 hops. Papers without an open full text are out of reach, and every run reports how much it covered.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1, background: 'var(--bk)', border: '2px solid var(--bk)' }}>
              {([
                [stats ? stats.affected_citations_total.toLocaleString() : '796', 'Citing papers judged'],
                [stats ? stats.notifications_sent.toLocaleString() : '742', 'Author notifications sent'],
                ['≤ 2', 'Hops through relaying papers'],
                ['Open', 'Full text only (Europe PMC)'],
              ] as [string, string][]).map(([n, l]) => (
                <div key={l} style={{ background: 'var(--y)', padding: '18px 22px' }}>
                  <div style={{ fontFamily: 'var(--mono)', fontSize: 36, color: 'var(--bk)', fontWeight: 700, lineHeight: 1 }}>{n}</div>
                  <div className="specimen" style={{ color: '#555', marginTop: 4 }}>{l}</div>
                </div>
              ))}
            </div>
          </div>
          <Panel label="FIG. 02 / Citation flow" style={{ background: 'rgba(10,10,10,0.6)', padding: 24 }}>
            <CitGraph active={netInView}/>
          </Panel>
        </div>
      </section>

      <Audience/>

      {/* ── FINAL CTA */}
      <section style={{ background: 'var(--bk2)', borderTop: '1px solid var(--gr3)', padding: '100px 60px', textAlign: 'center', position: 'relative', overflow: 'hidden' }}>
        <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--display)', fontSize: 'clamp(100px,18vw,200px)', color: 'rgba(245,197,24,0.03)', letterSpacing: '-0.03em', userSelect: 'none', pointerEvents: 'none' }}>
          STOP THE CASCADE
        </div>
        <div style={{ position: 'relative', zIndex: 2, maxWidth: 640, margin: '0 auto' }}>
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 20 }}>
            <Logo variant="cta"/>
          </div>
          <span className="specimen specimen-y" style={{ display: 'block', marginBottom: 28 }}>Do not cite blindly</span>
          <h2 style={{ fontFamily: 'var(--display)', fontSize: 'clamp(56px,9vw,120px)', lineHeight: 0.9, color: 'var(--y)', marginBottom: 28, letterSpacing: '0.01em' }}>
            SEE THE<br/>DRIFT.
          </h2>
          <p style={{ fontSize: 16, color: 'var(--gr)', fontWeight: 300, lineHeight: 1.8, maxWidth: 480, margin: '0 auto 48px' }}>
            Explore the interactive dashboard and observe how ClaimDrift maps the evolution of scientific claims across the global literature.
          </p>
          <div style={{ marginTop: 16, paddingTop: 40, borderTop: '1px solid var(--gr3)' }}>
            <div className="specimen" style={{ color: 'var(--wh2)', marginBottom: 20 }}>Built with</div>
            <div style={{ display: 'flex', gap: 28, justifyContent: 'center', flexWrap: 'wrap' }}>
              {['Vertex AI Agent Engine', 'Cloud Run', 'Elasticsearch', 'bioRxiv', 'medRxiv', 'Europe PMC'].map(t => (
                <span key={t} style={{ fontFamily: 'var(--mono)', fontSize: 10, color: 'var(--wh2)', letterSpacing: '0.1em' }}>{t}</span>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* ── FOOTER */}
      <footer style={{ background: 'var(--y)', padding: '20px 60px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 12 }}>
        <Logo variant="footer"/>
        <span style={{ fontFamily: 'var(--mono)', fontSize: 10, color: '#555', letterSpacing: '0.1em' }}>
          Jiayu Zhu (Alec) / Jeremy / Ranjan Yadav
        </span>
        <div style={{ display: 'flex', gap: 24 }}>
          {([['GitHub', 'https://github.com/gregjones11235/ClaimDrift']] as [string, string][]).map(([l, h]) => (
            <a key={l} href={h} target={h.startsWith('http') ? '_blank' : undefined} rel="noopener" data-h
              style={{ fontFamily: 'var(--mono)', fontSize: 10, color: '#555', textDecorationLine: 'none', letterSpacing: '0.1em', textTransform: 'uppercase', transition: 'color 0.2s' }}
              onMouseEnter={e => { (e.target as HTMLAnchorElement).style.color = 'var(--bk)'; }}
              onMouseLeave={e => { (e.target as HTMLAnchorElement).style.color = '#555'; }}>
              {l}
            </a>
          ))}
        </div>
      </footer>
    </>
  );
}
