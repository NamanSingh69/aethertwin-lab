import type { RenderMetrics } from "../../scene/SpatialRenderer";
import type { Metadata } from "../../lib/types";
import { metric } from "../../lib/status";
import { Icon } from "../Icon";

function heading(mode: string) {
  return {
    eyebrow:
      mode === "Explore"
        ? "SPATIAL EXPLORER"
        : mode === "Commission"
          ? "COMMISSIONING WORKSPACE"
          : mode.toUpperCase() + " WORKSPACE",
    title:
      mode === "Commission"
        ? "Know your reference."
        : mode === "Live"
          ? "Live spatial awareness."
          : "Your lab, in perspective.",
  };
}

export function ViewportHeading({
  mode,
  preset,
  onPreset,
}: {
  mode: string;
  preset: string;
  onPreset: (preset: string) => void;
}) {
  const { eyebrow, title } = heading(mode);
  return (
    <div className="viewport-heading">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
      </div>
      <div className="view-tools">
        <span className="axis-gizmo">
          <b>Z</b>
          <i />
          <em>Y</em>
          <strong>X</strong>
        </span>
        <label className="camera-select">
          <span className="sr-only">Camera preset</span>
          <select value={preset} onChange={(e) => onPreset(e.target.value)}>
            <option value="isometric">Isometric</option>
            <option value="top">Top view</option>
            <option value="sensor">Sensor view</option>
          </select>
          <Icon name="down" size={14} />
        </label>
      </div>
    </div>
  );
}

export function ViewportStats({
  scene,
  age,
  stale,
  metrics,
}: {
  scene: Metadata | null;
  age: number | null;
  stale: boolean;
  metrics: RenderMetrics | null;
}) {
  return (
    <div className="viewport-stats">
      <span>
        <i className="dot mint" />
        Frame{" "}
        <strong className="mono">
          {scene ? String(scene.frame_id).padStart(5, "0") : "—"}
        </strong>
      </span>
      <span>
        Observation age{" "}
        <strong className={`mono ${stale ? "amber-text" : ""}`}>
          {age === null
            ? "—"
            : age < 1
              ? `${Math.round(age * 1000)} ms`
              : `${age.toFixed(1)} s`}
        </strong>
      </span>
      <span>
        Render <strong className="mono">{metric(metrics?.fps, 0)} fps</strong>
      </span>
    </div>
  );
}

export function SceneEmpty({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <div className="scene-empty">
      <span className="scan-orb">
        <Icon name="scan" size={34} />
      </span>
      <h2>
        {error
          ? "Waiting for the local service"
          : "Connecting to your spatial twin"}
      </h2>
      <p>
        {error || "The measured map and sensor observations will appear here."}
      </p>
      {error && (
        <button className="button ghost" onClick={onRetry}>
          Reconnect
        </button>
      )}
    </div>
  );
}

export function ViewportWarnings({
  warnings,
  sourceError,
  sourceMode,
  source,
  hasScene,
  staleSeconds,
}: {
  warnings: string[];
  sourceError: string | null | undefined;
  sourceMode: string | undefined;
  source: string;
  hasScene: boolean;
  /** Seconds since the last observation when stale and not paused. */
  staleSeconds: number | null;
}) {
  return (
    <>
      {warnings.length > 0 && (
        <div className="sensor-warning" role="status">
          <Icon name="alert" size={16} />
          <span>{warnings.join(" · ")}</span>
        </div>
      )}
      {sourceError && (
        <div className="sensor-warning source-failure" role="alert">
          <Icon name="alert" size={16} />
          <span>
            {sourceMode} source unavailable: {sourceError}.{" "}
            {hasScene
              ? `Last ${source.toLowerCase()} observation retained.`
              : "No observations available."}
          </span>
        </div>
      )}
      {staleSeconds !== null && hasScene && (
        <div className="stale-warning">
          <Icon name="alert" size={16} />
          No new observation for {staleSeconds.toFixed(1)} s. Last scene remains
          visible.
        </div>
      )}
    </>
  );
}

export function PickBanner({
  target,
  height,
  onCancel,
}: {
  target: "start" | "goal";
  height: number | null;
  onCancel: () => void;
}) {
  return (
    <div className="pick-banner">
      <Icon name="target" />
      Pick {target} on world Z = {height?.toFixed(3)} m. Backend validates
      occupied and unknown space.
      <button onClick={onCancel}>Cancel</button>
    </div>
  );
}

export function ViewportFooter() {
  return (
    <div className="viewport-footer">
      <span>
        <Icon name="target" size={15} />
        Drag to orbit · Scroll to zoom · Click a track to inspect
      </span>
      <div className="semantic-key">
        <i style={{ background: "#21e5b0" }} />
        Person <i style={{ background: "#b388ff" }} />
        Cart <i style={{ background: "#ffd165" }} />
        New obstacle
      </div>
    </div>
  );
}
