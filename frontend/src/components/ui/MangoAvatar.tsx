/** Mango profile avatar — a small illustrated mango used across the app. */
export function MangoAvatar({ size = 24 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <defs>
        <radialGradient id="mangoBody" cx="38%" cy="30%" r="75%">
          <stop offset="0%" stopColor="#FFD66B" />
          <stop offset="55%" stopColor="#FFB43E" />
          <stop offset="100%" stopColor="#F58A2A" />
        </radialGradient>
      </defs>
      {/* mango body */}
      <path
        d="M12 4.2C7.6 4.2 4.4 7.7 4.4 13c0 5 3.4 8.4 7.6 8.4 4.4 0 7.6-3.4 7.6-8.4 0-5.3-3.2-8.8-7.6-8.8Z"
        fill="url(#mangoBody)"
      />
      {/* blush */}
      <ellipse cx="8.6" cy="14.4" rx="1.5" ry="1" fill="#F26B3A" opacity="0.55" />
      {/* highlight */}
      <ellipse cx="9.4" cy="10.4" rx="2.1" ry="1.4" fill="#FFFFFF" opacity="0.28" />
      {/* leaf */}
      <path
        d="M11.6 5.2C10 3.4 7.6 3.2 7 4.8c-.2 1 1.2 1.6 2.6 1.4 1 .6 2 .8 2 .8Z"
        fill="#4FAE5A"
      />
      <path d="M11.4 4.6C10.4 4 9.4 4 9 4.6" stroke="#3D8C45" strokeWidth="0.8" strokeLinecap="round" />
    </svg>
  );
}
