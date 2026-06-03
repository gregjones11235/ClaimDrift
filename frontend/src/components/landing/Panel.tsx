'use client';

import { CSSProperties, ReactNode } from 'react';

interface PanelProps {
  children: ReactNode;
  style?: CSSProperties;
  label?: string;
  labelRight?: string;
}

export default function Panel({ children, style, label, labelRight }: PanelProps) {
  return (
    <div style={{ position: 'relative', border: '1px solid var(--gr3)', ...style }}>
      <div className="corner-tl" /><div className="corner-tr" />
      <div className="corner-bl" /><div className="corner-br" />
      {label && (
        <div style={{ position: 'absolute', top: -10, left: 12, background: 'var(--bk)', padding: '0 8px' }}>
          <span className="specimen specimen-y">{label}</span>
        </div>
      )}
      {labelRight && (
        <div style={{ position: 'absolute', top: -10, right: 12, background: 'var(--bk)', padding: '0 8px' }}>
          <span className="specimen">{labelRight}</span>
        </div>
      )}
      {children}
    </div>
  );
}
