'use client';

import { useState, useEffect } from 'react';
import { useMousePos } from './hooks';

export default function LabCursor() {
  const pos = useMousePos();
  const [active, setActive] = useState(false);

  useEffect(() => {
    const on = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      setActive(!!target.closest('a,button,[data-h]'));
    };
    window.addEventListener('mouseover', on);
    return () => window.removeEventListener('mouseover', on);
  }, []);

  const s = active ? 32 : 20;

  return (
    <div style={{
      position: 'fixed',
      left: pos.x, top: pos.y,
      width: s, height: s,
      transform: 'translate(-50%,-50%)',
      pointerEvents: 'none',
      zIndex: 9999,
      transition: 'width 0.15s, height 0.15s',
    }}>
      <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} fill="none">
        <line x1={s/2} y1={0}     x2={s/2}   y2={s/2-3} stroke={active ? '#F5C518' : '#fff'} strokeWidth="1"/>
        <line x1={s/2} y1={s/2+3} x2={s/2}   y2={s}     stroke={active ? '#F5C518' : '#fff'} strokeWidth="1"/>
        <line x1={0}   y1={s/2}   x2={s/2-3} y2={s/2}   stroke={active ? '#F5C518' : '#fff'} strokeWidth="1"/>
        <line x1={s/2+3} y1={s/2} x2={s}     y2={s/2}   stroke={active ? '#F5C518' : '#fff'} strokeWidth="1"/>
        <circle
          cx={s/2} cy={s/2}
          r={active ? 5 : 1.5}
          fill={active ? 'rgba(245,197,24,0.3)' : '#F5C518'}
          stroke={active ? '#F5C518' : 'none'}
          strokeWidth="1"
        />
      </svg>
    </div>
  );
}
