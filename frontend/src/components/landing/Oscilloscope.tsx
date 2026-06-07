'use client';

interface OscilloscopeProps {
  color?: string;
  height?: number;
  speed?: number;
}

export default function Oscilloscope({ color = '#F5C518', height = 48, speed = 6 }: OscilloscopeProps) {
  const w = 660; // Three cycles of 220
  const amplitude = height * 0.45; // Increased vertically as requested
  const midY = height / 2;

  const p = (x: number, yNorm: number) => `${x.toFixed(1)},${(midY + yNorm * amplitude).toFixed(1)}`;

  const buildCycle = (offsetX: number) => [
    p(offsetX + 0, 0),
    p(offsetX + 30, 0),
    p(offsetX + 50, -1),
    p(offsetX + 70, 0),
    p(offsetX + 90, 0),
    p(offsetX + 110, 1),
    p(offsetX + 130, 0),
    p(offsetX + 160, 0),
    p(offsetX + 220, 0)
  ].join(' ');

  // pts covers 0 to 660
  const pts = [buildCycle(0), buildCycle(220), buildCycle(440)].join(' ');
  // pts2 covers 660 to 1320 for seamless looping
  const pts2 = [buildCycle(660), buildCycle(880), buildCycle(1100)].join(' ');

  return (
    <div style={{ overflow: 'hidden', height, position: 'relative' }}>
      <svg
        height={height}
        width={w * 2}
        style={{ animation: `wave-scroll ${speed}s linear infinite`, display: 'block' }}
      >
        <line x1="0" y1={midY} x2={w * 2} y2={midY} stroke={color} strokeWidth="0.5" opacity="0.3" />
        <polyline points={pts} fill="none" stroke={color} strokeWidth="1.5" opacity="0.8" />
        <polyline points={pts2} fill="none" stroke={color} strokeWidth="1.5" opacity="0.8" />
      </svg>
    </div>
  );
}
