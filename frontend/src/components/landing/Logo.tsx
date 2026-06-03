'use client';

type LogoVariant = 'nav' | 'hero' | 'cta' | 'footer';

interface LogoProps {
  variant: LogoVariant;
}

export default function Logo({ variant }: LogoProps) {
  if (variant === 'nav') {
    return (
      <svg width="210" height="58" viewBox="0 0 210 58" fill="none" aria-label="ClaimDrift" role="img">
        <defs>
          <linearGradient id="waveNavGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%"   stopColor="#555" stopOpacity={0}/>
            <stop offset="20%"  stopColor="#777"/>
            <stop offset="65%"  stopColor="#999"/>
            <stop offset="100%" stopColor="#444" stopOpacity={0.2}/>
          </linearGradient>
        </defs>
        <text x="0" y="20" fontFamily="Syne, sans-serif" fontSize="22" fontWeight="800" fill="#F5C518" letterSpacing="0.04em">CLAIM</text>
        <text x="0" y="40" fontFamily="Syne, sans-serif" fontSize="22" fontWeight="800" fill="#FFFFFF" letterSpacing="0.04em">DRIFT</text>
        <polyline points="108,28 120,28 130,16 140,28 150,28 162,44 174,28 200,28 210,28"
          fill="none" stroke="url(#waveNavGrad)" strokeWidth="1.5" strokeLinejoin="round"/>
        <circle cx="162" cy="44" r="6"   fill="none" stroke="#F5C518" strokeWidth="1.3"/>
        <circle cx="162" cy="44" r="1.5" fill="#F5C518"/>
        <line x1="162" y1="35" x2="162" y2="38" stroke="#F5C518" strokeWidth="1.1"/>
        <line x1="162" y1="50" x2="162" y2="53" stroke="#F5C518" strokeWidth="1.1"/>
        <line x1="153" y1="44" x2="156" y2="44" stroke="#F5C518" strokeWidth="1.1"/>
        <line x1="168" y1="44" x2="171" y2="44" stroke="#F5C518" strokeWidth="1.1"/>
      </svg>
    );
  }

  if (variant === 'hero') {
    return (
      <svg width="320" height="82" viewBox="0 0 320 82" fill="none" aria-label="ClaimDrift" role="img">
        <defs>
          <linearGradient id="waveHeroGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%"   stopColor="#555" stopOpacity={0.2}/>
            <stop offset="25%"  stopColor="#888"/>
            <stop offset="70%"  stopColor="#aaa"/>
            <stop offset="100%" stopColor="#555" stopOpacity={0.1}/>
          </linearGradient>
        </defs>
        <text x="0" y="32" fontFamily="Syne, sans-serif" fontSize="34" fontWeight="800" fill="#F5C518" letterSpacing="0.04em">CLAIM</text>
        <text x="0" y="62" fontFamily="Syne, sans-serif" fontSize="34" fontWeight="800" fill="#FFFFFF" letterSpacing="0.04em">DRIFT</text>
        <polyline points="168,46 185,46 200,28 216,46 230,46 248,56 265,46 305,46 320,46"
          fill="none" stroke="url(#waveHeroGrad)" strokeWidth="2" strokeLinejoin="round"/>
        <circle cx="248" cy="56" r="8"   fill="none" stroke="#F5C518" strokeWidth="1.5"/>
        <circle cx="248" cy="56" r="2.2" fill="#F5C518"/>
        <line x1="248" y1="44" x2="248" y2="48" stroke="#F5C518" strokeWidth="1.3"/>
        <line x1="248" y1="64" x2="248" y2="68" stroke="#F5C518" strokeWidth="1.3"/>
        <line x1="236" y1="56" x2="240" y2="56" stroke="#F5C518" strokeWidth="1.3"/>
        <line x1="256" y1="56" x2="260" y2="56" stroke="#F5C518" strokeWidth="1.3"/>
      </svg>
    );
  }

  if (variant === 'cta') {
    return (
      <svg width="260" height="72" viewBox="0 0 260 72" fill="none" aria-label="ClaimDrift" role="img">
        <defs>
          <linearGradient id="waveCtaGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%"   stopColor="#555" stopOpacity={0}/>
            <stop offset="20%"  stopColor="#777"/>
            <stop offset="65%"  stopColor="#999"/>
            <stop offset="100%" stopColor="#444" stopOpacity={0.2}/>
          </linearGradient>
        </defs>
        <text x="0" y="26" fontFamily="Syne, sans-serif" fontSize="28" fontWeight="800" fill="#F5C518" letterSpacing="0.04em">CLAIM</text>
        <text x="0" y="51" fontFamily="Syne, sans-serif" fontSize="28" fontWeight="800" fill="#FFFFFF" letterSpacing="0.04em">DRIFT</text>
        <polyline points="138,38 152,38 163,22 174,38 184,38 197,44 210,38 245,38 260,38"
          fill="none" stroke="url(#waveCtaGrad)" strokeWidth="1.8" strokeLinejoin="round"/>
        <circle cx="197" cy="44" r="7"   fill="none" stroke="#F5C518" strokeWidth="1.4"/>
        <circle cx="197" cy="44" r="2"   fill="#F5C518"/>
        <line x1="197" y1="33" x2="197" y2="37" stroke="#F5C518" strokeWidth="1.2"/>
        <line x1="197" y1="51" x2="197" y2="55" stroke="#F5C518" strokeWidth="1.2"/>
        <line x1="186" y1="44" x2="190" y2="44" stroke="#F5C518" strokeWidth="1.2"/>
        <line x1="204" y1="44" x2="208" y2="44" stroke="#F5C518" strokeWidth="1.2"/>
      </svg>
    );
  }

  // footer
  return (
    <svg width="180" height="50" viewBox="0 0 180 50" fill="none" aria-label="ClaimDrift" role="img">
      <defs>
        <linearGradient id="waveFootGrad" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%"   stopColor="#000" stopOpacity={0.1}/>
          <stop offset="30%"  stopColor="#000" stopOpacity={0.4}/>
          <stop offset="100%" stopColor="#000" stopOpacity={0.1}/>
        </linearGradient>
      </defs>
      <text x="0" y="17" fontFamily="Syne, sans-serif" fontSize="18" fontWeight="800" fill="#0A0A0A" letterSpacing="0.04em">CLAIM</text>
      <text x="0" y="34" fontFamily="Syne, sans-serif" fontSize="18" fontWeight="800" fill="#0A0A0A" letterSpacing="0.04em" opacity="0.45">DRIFT</text>
      <polyline points="96,24 106,24 113,14 120,24 127,24 135,30 143,24 170,24 180,24"
        fill="none" stroke="url(#waveFootGrad)" strokeWidth="1.2" strokeLinejoin="round"/>
      <circle cx="135" cy="30" r="5"   fill="none" stroke="#0A0A0A" strokeWidth="1"/>
      <circle cx="135" cy="30" r="1.4" fill="#0A0A0A"/>
      <line x1="135" y1="22" x2="135" y2="25" stroke="#0A0A0A" strokeWidth="0.9"/>
      <line x1="135" y1="35" x2="135" y2="38" stroke="#0A0A0A" strokeWidth="0.9"/>
      <line x1="127" y1="30" x2="130" y2="30" stroke="#0A0A0A" strokeWidth="0.9"/>
      <line x1="140" y1="30" x2="143" y2="30" stroke="#0A0A0A" strokeWidth="0.9"/>
    </svg>
  );
}
