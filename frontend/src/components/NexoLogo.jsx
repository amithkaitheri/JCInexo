import React from 'react';

/**
 * NexoLogo — the JCI NEXO brand mark (Option 5 from the design set).
 *
 * A hexagonal, fully-interconnected network: six outer nodes arranged in a
 * hexagon with edges + cross-diagonals, plus two gold accent nodes and small
 * teal inner nodes. Colors follow the JCI palette — navy, JCI blue (#009CDE),
 * teal, and JCI gold (#F2B705).
 *
 * Purely presentational and dependency-free; size is controlled by the caller
 * via the `size` prop (pixel width/height of the square SVG).
 */
export default function NexoLogo({ size = 34, title = 'JCI NEXO' }) {
  // Hexagon vertices around center (24,24), radius ~19.
  const V = {
    top: [24, 5],
    tr: [40.5, 14.5],
    br: [40.5, 33.5],
    bottom: [24, 43],
    bl: [7.5, 33.5],
    tl: [7.5, 14.5],
  };
  const outer = [V.top, V.tr, V.br, V.bottom, V.bl, V.tl];

  // All pairwise edges among the 6 outer nodes (hexagon sides + diagonals).
  const edges = [];
  for (let i = 0; i < outer.length; i += 1) {
    for (let j = i + 1; j < outer.length; j += 1) {
      edges.push([outer[i], outer[j]]);
    }
  }

  // Node colors (JCI palette).
  const NAVY = '#1B3B6F';
  const BLUE = '#009CDE';
  const TEAL = '#5BB0B5';
  const GOLD = '#F2B705';

  const nodeColors = [BLUE, NAVY, NAVY, NAVY, TEAL, BLUE]; // per outer vertex

  return (
    <svg
      viewBox="0 0 48 48"
      width={size}
      height={size}
      role="img"
      aria-label={title}
    >
      <title>{title}</title>
      {/* Mesh edges */}
      <g stroke="#2C63A5" strokeWidth="1.6" strokeLinecap="round" opacity="0.9">
        {edges.map(([a, b], idx) => (
          <line key={idx} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} />
        ))}
      </g>

      {/* Inner accent nodes (teal + gold) */}
      <circle cx="19" cy="25" r="2.1" fill={TEAL} />
      <circle cx="27" cy="22" r="3" fill={GOLD} />
      <circle cx="33.5" cy="19" r="3" fill={GOLD} />

      {/* Outer hexagon nodes */}
      {outer.map(([x, y], idx) => (
        <circle key={idx} cx={x} cy={y} r="3.4" fill={nodeColors[idx]} />
      ))}
    </svg>
  );
}
