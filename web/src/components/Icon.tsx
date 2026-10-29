export function Icon({ name, size = 19 }: { name: string; size?: number }) {
  const paths: Record<string, React.ReactNode> = {
    layers: (
      <>
        <path d="m12 3 9 5-9 5-9-5 9-5Z" />
        <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
      </>
    ),
    scan: (
      <>
        <path d="M7 3H3v4M17 3h4v4M3 17v4h4M21 17v4h-4" />
        <circle cx="12" cy="12" r="5" />
        <path d="m12 12 5-5" />
      </>
    ),
    camera: (
      <>
        <path d="M3 7h5l2-3h4l2 3h5v13H3Z" />
        <circle cx="12" cy="13" r="4" />
      </>
    ),
    cube: (
      <>
        <path d="m12 2 9 5v10l-9 5-9-5V7Z" />
        <path d="m3 7 9 5 9-5M12 12v10" />
      </>
    ),
    flow: (
      <>
        <path d="M3 7h14m-4-4 4 4-4 4M21 17H7m4-4-4 4 4 4" />
      </>
    ),
    route: (
      <>
        <circle cx="5" cy="5" r="2" />
        <circle cx="19" cy="19" r="2" />
        <path d="M7 5h9a4 4 0 0 1 0 8H8a3 3 0 0 0 0 6h9" />
      </>
    ),
    fence: (
      <>
        <path d="M4 20V4l8-2 8 2v16l-8 2Z" />
        <path d="M4 4l8 2 8-2M12 6v16" />
      </>
    ),
    person: (
      <>
        <circle cx="12" cy="4" r="2" />
        <path d="m7 12 2-5h6l2 5M12 8v6m0 0-4 7m4-7 4 7" />
      </>
    ),
    chart: (
      <>
        <path d="M3 3v18h18M6 16l4-5 4 2 6-8" />
      </>
    ),
    play: <path d="m8 4 12 8-12 8Z" />,
    pause: (
      <>
        <path d="M8 4v16M16 4v16" />
      </>
    ),
    step: (
      <>
        <path d="m5 5 10 7-10 7ZM19 5v14" />
      </>
    ),
    down: <path d="m6 9 6 6 6-6" />,
    close: <path d="m6 6 12 12M18 6 6 18" />,
    check: <path d="m5 12 4 4L19 6" />,
    alert: (
      <>
        <path d="m12 3 10 18H2Z" />
        <path d="M12 9v5m0 3v1" />
      </>
    ),
    target: (
      <>
        <circle cx="12" cy="12" r="7" />
        <circle cx="12" cy="12" r="2" />
        <path d="M12 2v3m0 14v3M2 12h3m14 0h3" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" />
      </>
    ),
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    settings: (
      <>
        <path d="M4 7h16M4 17h16" />
        <circle cx="9" cy="7" r="3" />
        <circle cx="15" cy="17" r="3" />
      </>
    ),
    grid: (
      <>
        <path d="M3 3h18v18H3ZM3 9h18M3 15h18M9 3v18M15 3v18" />
      </>
    ),
    link: (
      <>
        <path d="m10 14 4-4M8 16l-2 2a3 3 0 0 1-4-4l4-4m10-2 2-2a3 3 0 0 1 4 4l-4 4" />
      </>
    ),
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.55"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {paths[name] ?? paths.cube}
    </svg>
  );
}
