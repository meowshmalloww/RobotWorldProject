export function Logo({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden>
      <path d="M16 2 28 9v14L16 30 4 23V9L16 2z" stroke="var(--accent)" strokeWidth="2" strokeLinejoin="round" fill="rgba(76,141,255,0.10)" />
      <path d="M16 2v28M4 9l24 14M28 9L4 23" stroke="var(--accent)" strokeWidth="1.1" opacity="0.55" />
      <circle cx="16" cy="16" r="3.1" fill="var(--accent)" />
    </svg>
  );
}
