import type { HaloSnapshot, LayerName, Layers, Track } from "../../lib/types";
import { alignmentSummary, type Appearance } from "../../lib/appearance";
import type { SensorIndicator } from "../../lib/status";
import type { SourceOption } from "../../lib/sources";
import { Icon } from "../Icon";
import { HaloMirror } from "../HaloMirror";
import { SourceSelect } from "./SourceSelect";
import { WORKSPACES } from "./TopBar";

export const layerInfo: Array<{
  id: LayerName;
  name: string;
  icon: string;
  color?: string;
}> = [
  { id: "map", name: "Measured twin", icon: "cube" },
  { id: "voxels", name: "Semantic voxels", icon: "scan", color: "#42bded" },
  { id: "tracks", name: "Tracked entities", icon: "person", color: "#3be8b8" },
  { id: "path", name: "Safe trajectory", icon: "route", color: "#51efbf" },
  { id: "flow", name: "Scene flow", icon: "flow", color: "#ddd68b" },
  { id: "geofences", name: "Protected zones", icon: "fence", color: "#e9b458" },
  { id: "risk", name: "Risk hotspots", icon: "alert", color: "#ed9774" },
  { id: "traffic", name: "Traffic history", icon: "chart", color: "#b998eb" },
  { id: "rgbd", name: "RGB-D points", icon: "camera" },
  { id: "capture", name: "Photo capture", icon: "camera" },
];

function SensorRow({
  icon,
  name,
  status,
}: {
  icon: string;
  name: string;
  status: SensorIndicator;
}) {
  return (
    <div className="sensor-row">
      <Icon name={icon} />
      <span>{name}</span>
      <span
        className={`status-light ${status.tone}`}
        title={status.title}
        role="img"
        aria-label={status.label}
      />
    </div>
  );
}

export function LayerDock(props: {
  open: boolean;
  mode: string;
  onMode: (mode: string) => void;
  source: string;
  role: string | undefined;
  options: SourceOption[];
  sourceDisabled: boolean;
  onChooseSource: (option: SourceOption) => void;
  tracks: Track[];
  selected: number | null;
  onSelectTrack: (id: number) => void;
  lidarStatus: SensorIndicator;
  depthStatus: SensorIndicator;
  halo: HaloSnapshot | undefined;
  haloOpen: boolean;
  onToggleHalo: () => void;
  warning: string | undefined;
  layers: Layers;
  onLayer: (name: LayerName, visible: boolean) => void;
  cutaway: boolean;
  onCutaway: (enabled: boolean) => void;
  routeOpen: boolean;
  onToggleRoute: () => void;
  zonesDisabled: boolean;
  zoneCount: number;
  onOpenZones: () => void;
  graphOpen: boolean;
  onToggleGraph: () => void;
  mapRevision: string | undefined;
  capture?: Appearance | null;
}) {
  const { source } = props;
  return (
    <aside className={`layer-dock dock ${props.open ? "open" : ""}`}>
      <section className="responsive-workspace" aria-label="Workspace menu">
        <div className="section-title">Workspace</div>
        <nav aria-label="All workspaces">
          {WORKSPACES.map((tab) => (
            <button
              className={props.mode === tab ? "active" : ""}
              key={tab}
              onClick={() => props.onMode(tab)}
            >
              {tab}
            </button>
          ))}
        </nav>
        <label>
          Data source
          <SourceSelect
            ariaLabel="Mobile data source"
            options={props.options}
            currentLabel={source}
            disabled={props.sourceDisabled}
            onChoose={props.onChooseSource}
          />
        </label>
        <label className="mobile-track-picker">
          Inspect an entity
          <select
            aria-label="Select tracked entity"
            value={props.selected ?? ""}
            onChange={(event) => {
              if (event.target.value)
                props.onSelectTrack(Number(event.target.value));
            }}
          >
            <option value="">Select a track</option>
            {props.tracks.slice(0, 256).map((track) => (
              <option key={track.track_id} value={track.track_id}>
                {track.semantic_name.replaceAll("_", " ")} #{track.track_id}
              </option>
            ))}
          </select>
        </label>
        <p className="caption mobile-session-role">
          {props.role ?? "Guest"} session · {source}
        </p>
      </section>
      <div className="section-title">
        Sensors <span className="eyebrow">{source.toUpperCase()}</span>
      </div>
      <SensorRow icon="scan" name="3D LiDAR" status={props.lidarStatus} />
      <SensorRow icon="camera" name="Depth camera" status={props.depthStatus} />
      <button
        className="sensor-row halo-toggle"
        aria-expanded={props.haloOpen}
        onClick={props.onToggleHalo}
      >
        <Icon name="cube" />
        <span>Virtual halo</span>
        <span className="sensor-label">
          {props.halo?.state_name ?? "Optional"}
        </span>
      </button>
      {props.haloOpen && <HaloMirror halo={props.halo} />}
      <div className="sensor-note">
        <Icon name="alert" size={16} />
        <span>
          {props.warning ??
            (source === "Simulation"
              ? "Synthetic data · no hardware claim"
              : "Physical validation not reported")}
        </span>
      </div>
      <div className="section-title divider">
        Layers{" "}
        <span className="muted">
          {Object.values(props.layers).filter(Boolean).length}
        </span>
      </div>
      <div className="layer-list">
        {layerInfo.map((layer) => (
          <label className="layer-row" key={layer.id}>
            <input
              type="checkbox"
              disabled={layer.id === "capture" && !props.capture}
              checked={props.layers[layer.id]}
              onChange={(e) => props.onLayer(layer.id, e.target.checked)}
            />
            <span className="custom-check">
              <Icon name="check" size={12} />
            </span>
            <span className="layer-icon" style={{ color: layer.color }}>
              <Icon name={layer.icon} size={17} />
            </span>
            <span>{layer.name}</span>
          </label>
        ))}
      </div>
      <p className="caption capture-caption">
        {props.capture
          ? `Photo capture (visual only): ${alignmentSummary(props.capture).toLowerCase()}.`
          : "No photo capture loaded. Add one with --appearance (docs/LAB_APPEARANCE.md)."}
      </p>
      <label className="cutaway-switch">
        <span>Cutaway view</span>
        <input
          type="checkbox"
          role="switch"
          checked={props.cutaway}
          onChange={(e) => props.onCutaway(e.target.checked)}
        />
        <i />
      </label>
      <p className="caption layer-caption">
        Display layers only. All backend algorithms remain active.
      </p>
      <div className="section-title divider">Workspace tools</div>
      <button
        className={`tool-row ${props.routeOpen ? "active" : ""}`}
        onClick={props.onToggleRoute}
      >
        <Icon name="route" />
        <span>Plan a safe route</span>
        <Icon name="arrow" size={14} />
      </button>
      <button
        className="tool-row"
        onClick={props.onOpenZones}
        disabled={props.zonesDisabled}
      >
        <Icon name="fence" />
        <span>Protected zones</span>
        <span className="count">{props.zoneCount}</span>
      </button>
      <button
        className={`tool-row ${props.graphOpen ? "active" : ""}`}
        onClick={props.onToggleGraph}
      >
        <Icon name="chart" />
        <span>Pipeline measurements</span>
        <Icon name="down" size={14} />
      </button>
      <div className="dock-bottom">
        <div className="tiny-label">WORLD COORDINATES</div>
        <div className="coordinate-key">
          <b className="axis-x">X</b>
          <b className="axis-y">Y</b>
          <b className="axis-z">Z</b>
          <span>Right-handed · Z up · m</span>
        </div>
        <div className="map-version">
          Map{" "}
          <span className="mono">
            {props.mapRevision?.slice(0, 12) ?? "unavailable"}
          </span>
        </div>
      </div>
    </aside>
  );
}
