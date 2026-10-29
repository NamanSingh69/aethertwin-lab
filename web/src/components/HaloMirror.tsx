import type { HaloSnapshot } from "../lib/types";
import { metric } from "../lib/status";
export function haloPixelPosition(index: number): [number, number] {
  const angle = -Math.PI / 2 - (index / 24) * Math.PI * 2;
  return [
    Number((48 + 31 * Math.cos(angle)).toFixed(2)),
    Number((48 + 31 * Math.sin(angle)).toFixed(2)),
  ];
}
export function validHaloPixels(halo: HaloSnapshot | null | undefined) {
  return (
    halo?.pixel_count === 24 &&
    halo.pixels?.length === 24 &&
    halo.pixels.every(
      (pixel) =>
        pixel.length === 3 &&
        pixel.every(
          (channel) =>
            Number.isInteger(channel) && channel >= 0 && channel <= 255,
        ),
    )
  );
}
export function HaloMirror({
  halo,
}: {
  halo: HaloSnapshot | null | undefined;
}) {
  const valid = validHaloPixels(halo);
  const state = valid ? halo!.state_name : "not reported";
  return (
    <section className="halo-mirror" aria-label="Virtual telemetry halo mirror">
      <div className="halo-preview">
        <svg
          viewBox="0 0 96 96"
          role="img"
          aria-label={`24-pixel software mirror: ${state}. Index zero is rig forward, positive Y is left.`}
        >
          <circle cx="48" cy="48" r="23" fill="#0a1420" stroke="#223b50" />
          {Array.from({ length: 24 }, (_, index) => {
            const [x, y] = haloPixelPosition(index);
            return (
              <circle
                data-index={index}
                key={index}
                cx={x}
                cy={y}
                r="2.9"
                fill={valid ? `rgb(${halo!.pixels[index].join(",")})` : "none"}
                stroke="#2f4559"
                strokeWidth=".6"
              />
            );
          })}
          <text x="48" y="8" textAnchor="middle" fill="#5c83a8" fontSize="6">
            +X FWD
          </text>
          <text x="48" y="45" textAnchor="middle" fill="#87a4c0" fontSize="7">
            VIRTUAL
          </text>
          <text x="48" y="55" textAnchor="middle" fill="#4f7598" fontSize="6">
            24 PIXELS
          </text>
          <text x="4" y="51" fill="#5c83a8" fontSize="6">
            +Y
          </text>
        </svg>
      </div>
      <div className="halo-status">
        <strong
          className={
            halo?.stale || halo?.watchdog || halo?.disconnected
              ? "amber-text"
              : ""
          }
        >
          {state.replaceAll("_", " ")}
        </strong>
        <span>
          {halo?.source_age_ms !== null && halo?.source_age_ms !== undefined
            ? `Source age ${metric(halo.source_age_ms, 0)} ms`
            : "Source age not reported"}
        </span>
        <span>
          {halo?.nearest_track_id !== null &&
          halo?.nearest_track_id !== undefined
            ? `Target #${halo.nearest_track_id} · ${metric(halo.distance_m)} m`
            : "No target reported"}
        </span>
        <span className="halo-physical">
          {halo?.physical_verified
            ? "Physical output verified"
            : "Physical output not verified"}
        </span>
      </div>
      {halo?.reason && <p className="caption halo-reason">{halo.reason}</p>}
    </section>
  );
}
