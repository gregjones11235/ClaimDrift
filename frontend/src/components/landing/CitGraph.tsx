'use client';

import { useEffect, useRef } from 'react';

interface CitGraphProps {
  active: boolean;
}

export default function CitGraph({ active }: CitGraphProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!active || typeof window === 'undefined') return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Increased canvas height for a taller box
    canvas.width = 600;
    canvas.height = 320;
    const W = canvas.width, H = canvas.height;
    const MID = H / 2;

    const CX = [70, 220, 380, 540];

    // Spread the vertical positions slightly and increased node sizes so text is readable
    const NODES = [
      { id:0, x:CX[0], y:MID,         r:28, fs:9,  label:'PREPRINT', fill:'#F5C518', tc:'#0A0A0A', glow:'#F5C518' },
      { id:9, x:CX[1], y:MID,         r:18, fs:8,  label:'DRIFT',    fill:'#E5383B', tc:'#fff',    glow:'#E5383B' },
      { id:1, x:CX[2], y:MID-90,      r:14, fs:10, label:'A',  fill:'#1c1c1a', tc:'#888', border:'#3a3a38', glow:'#888' },
      { id:2, x:CX[2], y:MID,         r:14, fs:10, label:'B',  fill:'#1c1c1a', tc:'#888', border:'#3a3a38', glow:'#888' },
      { id:3, x:CX[2], y:MID+90,      r:14, fs:10, label:'C',  fill:'#1c1c1a', tc:'#888', border:'#3a3a38', glow:'#888' },
      { id:4, x:CX[3], y:MID-135,     r:11, fs:9,  label:'D',  fill:'#141412', tc:'#666', border:'#2a2a28', glow:'#555' },
      { id:5, x:CX[3], y:MID-65,      r:11, fs:9,  label:'E',  fill:'#141412', tc:'#666', border:'#2a2a28', glow:'#555' },
      { id:6, x:CX[3], y:MID,         r:11, fs:9,  label:'F',  fill:'#141412', tc:'#666', border:'#2a2a28', glow:'#555' },
      { id:7, x:CX[3], y:MID+65,      r:11, fs:9,  label:'G',  fill:'#141412', tc:'#666', border:'#2a2a28', glow:'#555' },
      { id:8, x:CX[3], y:MID+135,     r:11, fs:9,  label:'H',  fill:'#141412', tc:'#666', border:'#2a2a28', glow:'#555' },
    ];

    const EDGES = [
      { a:0, b:9 },
      { a:9, b:1 }, { a:9, b:2 }, { a:9, b:3 },
      { a:1, b:4 }, { a:1, b:5 },
      { a:2, b:6 },
      { a:3, b:7 }, { a:3, b:8 },
    ];

    const WRAP = [4,5,6,7,8];

    const getNode = (id: number) => NODES.find(n => n.id === id)!;
    const edgesFrom = (id: number) => EDGES.filter(e => e.a === id);

    const nodeGlow: Record<number, number> = {};
    NODES.forEach(n => nodeGlow[n.id] = 0);

    let particles: any[] = [];
    let tick = 0;
    let animFrame: number;

    const COL_COLORS: Record<number, string> = {
      0: '#F5C518',
      1: '#E5383B',
      2: '#6a9fd8',
      3: '#4a7a5a',
    };

    const nodeColor = (id: number) => {
      if (id === 0) return COL_COLORS[0];
      if (id === 9) return COL_COLORS[1];
      if ([1,2,3].includes(id)) return COL_COLORS[2];
      return COL_COLORS[3];
    };

    const spawnParticle = (fromId: number, toId: number, delay = 0) => {
      if (particles.length > 80) return;
      const a = getNode(fromId), b = getNode(toId);
      const dx = b.x-a.x, dy = b.y-a.y;
      const dist = Math.sqrt(dx*dx+dy*dy);
      particles.push({
        ax:a.x, ay:a.y, bx:b.x, by:b.y,
        fromId, toId, dist,
        progress: -delay,
        color: nodeColor(fromId),
      });
    };

    spawnParticle(0, 9,  0);
    spawnParticle(0, 9,  30);
    spawnParticle(0, 9,  60);

    const drawGraph = () => {
      EDGES.forEach(e => {
        const a = getNode(e.a), b = getNode(e.b);
        const dx=b.x-a.x, dy=b.y-a.y, dist=Math.sqrt(dx*dx+dy*dy);
        const ux=dx/dist, uy=dy/dist;
        const x1=a.x+ux*(a.r+1), y1=a.y+uy*(a.r+1);
        const x2=b.x-ux*(b.r+4), y2=b.y-uy*(b.r+4);

        ctx.beginPath();
        ctx.moveTo(x1,y1); ctx.lineTo(x2,y2);
        // Changed to #333330 so it is visible against the black background
        ctx.strokeStyle='#333330';
        ctx.lineWidth=1; 
        ctx.stroke();

        const ang=Math.atan2(uy,ux);
        ctx.beginPath();
        ctx.moveTo(x2,y2);
        ctx.lineTo(x2-5*Math.cos(ang-0.4), y2-5*Math.sin(ang-0.4));
        ctx.lineTo(x2-5*Math.cos(ang+0.4), y2-5*Math.sin(ang+0.4));
        ctx.closePath();
        ctx.fillStyle='#333330'; 
        ctx.fill();
      });

      NODES.forEach(n => {
        const g = nodeGlow[n.id];
        if (g > 0.01) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 4 + g*8, 0, Math.PI*2);
          ctx.strokeStyle = (n.glow||'#888') + Math.round(g*180).toString(16).padStart(2,'0');
          ctx.lineWidth = 1;
          ctx.stroke();
        }

        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI*2);
        ctx.fillStyle = n.fill; ctx.fill();
        if (n.border) { ctx.strokeStyle=n.border; ctx.lineWidth=0.5; ctx.stroke(); }

        ctx.fillStyle = n.tc;
        ctx.font = `700 ${n.fs}px 'var(--mono)',monospace`;
        ctx.textAlign='center'; ctx.textBaseline='middle';
        ctx.fillText(n.label, n.x, n.y);
      });
    };

    const drawParticle = (p: any) => {
      if (p.progress < 0) return;
      const t = Math.min(p.progress / p.dist, 1);
      const x = p.ax + (p.bx-p.ax)*t;
      const y = p.ay + (p.by-p.ay)*t;

      const tailFrac = 24 / p.dist;
      const t0 = Math.max(0, t - tailFrac);
      const tx = p.ax + (p.bx-p.ax)*t0;
      const ty = p.ay + (p.by-p.ay)*t0;

      const grad = ctx.createLinearGradient(tx, ty, x, y);
      grad.addColorStop(0, 'transparent');
      grad.addColorStop(1, p.color);
      ctx.beginPath(); ctx.moveTo(tx,ty); ctx.lineTo(x,y);
      ctx.strokeStyle=grad; ctx.lineWidth=1.8; ctx.stroke();

      ctx.beginPath(); ctx.arc(x,y,2.8,0,Math.PI*2);
      ctx.fillStyle=p.color; ctx.fill();

      if (t > 0.6) {
        nodeGlow[p.toId] = Math.max(nodeGlow[p.toId], (t-0.6)/0.4);
      }
    };

    const updateParticles = () => {
      const next: any[] = [];
      particles.forEach(p => {
        p.progress += 2.2;
        if (p.progress < 0) { next.push(p); return; }
        const t = p.progress / p.dist;
        if (t >= 1) {
          nodeGlow[p.toId] = 1.0;
          const outs = edgesFrom(p.toId);
          outs.forEach((e, i) => spawnParticle(e.a, e.b, i * 12));
          if (WRAP.includes(p.toId)) {
            const delay = 20 + Math.random() * 40;
            spawnParticle(0, 9, delay);
          }
          return;
        }
        next.push(p);
      });
      particles = next;
    };

    const decayGlow = () => {
      NODES.forEach(n => {
        nodeGlow[n.id] = Math.max(0, nodeGlow[n.id] - 0.025);
      });
    };

    const topUp = () => {
      if (tick % 110 === 0) spawnParticle(0, 9, 0);
    };

    const frame = () => {
      ctx.clearRect(0, 0, W, H);
      decayGlow();
      updateParticles();
      topUp();
      drawGraph();
      particles.forEach(drawParticle);
      tick++;
      animFrame = requestAnimationFrame(frame);
    };

    frame();

    return () => {
      cancelAnimationFrame(animFrame);
    };
  }, [active]);

  return (
    <div style={{ opacity: active ? 1 : 0, transition: 'opacity 0.6s ease' }}>
      <canvas ref={canvasRef} style={{ width: '100%', height: 'auto', display: 'block' }} />
    </div>
  );
}
