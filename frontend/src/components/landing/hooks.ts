'use client';

import { useState, useEffect, useRef } from 'react';

export function useInView(threshold = 0.12): [React.RefObject<HTMLDivElement | null>, boolean] {
  const ref = useRef<HTMLDivElement>(null);
  const [inView, setInView] = useState(false);
  useEffect(() => {
    // A tall block can never reach a high intersection ratio in a short
    // viewport, so a single threshold like 0.2 may never fire and the panel
    // stays stuck in its inactive (0%) state. Watch both 0 and the requested
    // threshold, and trip as soon as ANY part is visible — combined with a
    // bottom rootMargin this reads as "the block has scrolled into view".
    const obs = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && entry.intersectionRatio > 0) {
          setInView(true);
          obs.disconnect();
        }
      },
      { threshold: [0, threshold], rootMargin: '0px 0px -10% 0px' }
    );
    if (ref.current) obs.observe(ref.current);
    return () => obs.disconnect();
  }, [threshold]);
  return [ref, inView];
}

export function useCounter(target: number, duration: number, active: boolean): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    if (!active) return;
    let raf: number;
    const start = performance.now();
    const tick = (now: number) => {
      const p = Math.min((now - start) / duration, 1);
      setN(Math.round((1 - Math.pow(1 - p, 4)) * target));
      if (p < 1) raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active, target, duration]);
  return n;
}

export function useMousePos(): { x: number; y: number } {
  const [pos, setPos] = useState({ x: 0, y: 0 });
  useEffect(() => {
    const fn = (e: MouseEvent) => setPos({ x: e.clientX, y: e.clientY });
    window.addEventListener('mousemove', fn, { passive: true });
    return () => window.removeEventListener('mousemove', fn);
  }, []);
  return pos;
}

export function useScrollY(): number {
  const [scrollY, setScrollY] = useState(0);
  useEffect(() => {
    const fn = () => setScrollY(window.scrollY);
    window.addEventListener('scroll', fn, { passive: true });
    return () => window.removeEventListener('scroll', fn);
  }, []);
  return scrollY;
}
