export const FONTS = `
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=Space+Mono:ital,wght@0,400;0,700;1,400&family=Syne:wght@700;800&display=swap');
`;

export const GLOBAL_CSS = `
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; font-size: 16px; }

:root {
  --y:  #F5C518;
  --yd: #C49D0A;
  --yb: rgba(245,197,24,0.08);
  --bk: #0A0A0A;
  --bk2:#111111;
  --bk3:#1A1A1A;
  --wh: #F7F5EE;
  --wh2:#EDEBE3;
  --gr: #888882;
  --gr2:#555550;
  --gr3:#333330;
  --rd: #E5383B;
  --grn:#3ECF8E;
  --bl: #4A9EFF;
  --mono:'Space Mono', monospace;
  --display:'Syne', sans-serif;
  --sans:'Space Grotesk', system-ui, sans-serif;
  --grid-color: rgba(245,197,24,0.07);
  --cross-color: rgba(245,197,24,0.15);
}

body {
  font-family: var(--sans);
  background: var(--bk);
  color: var(--wh);
  overflow-x: hidden;
  line-height: 1.6;
  cursor: crosshair;
}

::selection { background: var(--y); color: var(--bk); }
::-webkit-scrollbar { width: 3px; }
::-webkit-scrollbar-track { background: var(--bk); }
::-webkit-scrollbar-thumb { background: var(--y); }

.lab-bg {
  position: fixed; inset: 0; pointer-events: none; z-index: 0;
  background-image:
    linear-gradient(var(--grid-color) 1px, transparent 1px),
    linear-gradient(90deg, var(--grid-color) 1px, transparent 1px),
    linear-gradient(var(--cross-color) 1px, transparent 1px),
    linear-gradient(90deg, var(--cross-color) 1px, transparent 1px);
  background-size: 20px 20px, 20px 20px, 100px 100px, 100px 100px;
}

.lab-bg::after {
  content: '';
  position: absolute; inset: 0;
  background-image: radial-gradient(circle, rgba(245,197,24,0.25) 1px, transparent 1px);
  background-size: 100px 100px;
  background-position: 0 0;
}

.scanline {
  position: fixed; inset: 0; pointer-events: none; z-index: 1;
  background: repeating-linear-gradient(
    0deg,
    transparent,
    transparent 2px,
    rgba(0,0,0,0.03) 2px,
    rgba(0,0,0,0.03) 4px
  );
}

.specimen {
  font-family: var(--mono);
  font-size: 9px;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--gr);
}
.specimen-y { color: var(--y); }
.specimen-r { color: var(--rd); }
.specimen-g { color: var(--grn); }

.corner-tl, .corner-tr, .corner-bl, .corner-br {
  position: absolute;
  width: 16px; height: 16px;
}
.corner-tl { top: 0; left: 0; border-top: 1.5px solid var(--y); border-left: 1.5px solid var(--y); }
.corner-tr { top: 0; right: 0; border-top: 1.5px solid var(--y); border-right: 1.5px solid var(--y); }
.corner-bl { bottom: 0; left: 0; border-bottom: 1.5px solid var(--y); border-left: 1.5px solid var(--y); }
.corner-br { bottom: 0; right: 0; border-bottom: 1.5px solid var(--y); border-right: 1.5px solid var(--y); }

@keyframes wave-scroll { from { transform: translateX(0); } to { transform: translateX(-50%); } }
@keyframes fade-up { from { opacity: 0; transform: translateY(28px); } to { opacity: 1; transform: translateY(0); } }
@keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0; } }
@keyframes pulse-ring { 0% { transform: scale(1); opacity: 0.6; } 100% { transform: scale(2.4); opacity: 0; } }
@keyframes marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
.fade-up { animation: fade-up 0.7s ease both; }
`;
