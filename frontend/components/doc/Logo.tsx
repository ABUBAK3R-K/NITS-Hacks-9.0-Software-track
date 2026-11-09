import { useId } from 'react';

// J.A.R.V.I.S. mark: three small alert dots on the left converge into one node (many alerts, one incident),
// and an accent check sits inside the node (the cause, proven). The check is knocked out of the node with a
// mask so the gap stays transparent on any background. Drawn on a 24px grid for navbar use.
const NODE = { cx: 15.5, cy: 12, r: 7.5 };
const DOTS = [
  [3, 4.5],
  [2.5, 12],
  [3, 19.5],
] as const;
const LINKS = 'M4.4 5.6L8.9 8.9M4 12H8M4.4 18.4L8.9 15.1';
const CHECK = 'M12.25 12.25l2.25 2.25L19 9.75';

export function Logo({ size = 22, title = 'J.A.R.V.I.S.' }: { size?: number; title?: string }) {
  const id = useId().replace(/:/g, '');
  const maskId = `cs-logo-cut-${id}`;
  return (
    <svg role="img" aria-label={title} width={size} height={size} viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
      <title>{title}</title>
      <mask id={maskId} maskUnits="userSpaceOnUse" x="0" y="0" width="24" height="24">
        <rect width="24" height="24" fill="#fff" />
        <path d={CHECK} fill="none" stroke="#000" strokeWidth={4.25} strokeLinecap="round" strokeLinejoin="round" />
      </mask>
      {DOTS.map(([cx, cy]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={1.75} fill="currentColor" />
      ))}
      <path d={LINKS} fill="none" stroke="currentColor" strokeWidth={1.25} strokeLinecap="round" opacity={0.55} />
      <circle cx={NODE.cx} cy={NODE.cy} r={NODE.r} fill="currentColor" mask={`url(#${maskId})`} />
      <path d={CHECK} fill="none" stroke="var(--accent, currentColor)" strokeWidth={2.1} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
