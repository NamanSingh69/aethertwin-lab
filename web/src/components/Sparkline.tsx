export function Sparkline({
  values,
  color = "#5da8f5",
  height = 42,
}: {
  values: number[];
  color?: string;
  height?: number;
}) {
  const series = values.filter(Number.isFinite).slice(-100);
  if (series.length < 2)
    return <div className="empty-chart">Awaiting measurements</div>;
  const min = 0,
    max = Math.max(...series, 1);
  const points = series
    .map(
      (v, i) =>
        `${(i / (series.length - 1)) * 240},${height - 4 - ((v - min) / (max - min)) * (height - 8)}`,
    )
    .join(" ");
  return (
    <svg
      className="sparkline"
      viewBox={`0 0 240 ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Last ${series.length} measured processing latencies`}
    >
      <path d={`M0 ${height - 1}H240`} stroke="#203040" />
      <polyline
        points={points}
        fill="none"
        stroke={color}
        strokeWidth="1.7"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}
