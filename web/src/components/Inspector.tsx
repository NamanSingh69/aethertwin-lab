import { useState } from "react";
import type { Metadata, Track } from "../lib/types";
import { descriptorNames, palette, semanticNames } from "../lib/types";
import { centerZoneClearance, positionStd } from "../lib/geometry";
import { metric } from "../lib/status";
import { Icon } from "./Icon";
import { Sparkline } from "./Sparkline";
export function Inspector({
  scene,
  selected,
  onSelect,
  onFocus,
  onClear,
  latencies,
}: {
  scene: Metadata | null;
  selected: number | null;
  onSelect: (id: number) => void;
  onFocus: (id: number) => void;
  onClear: () => void;
  latencies: number[];
}) {
  const [tab, setTab] = useState("Details");
  const tracks = scene?.tracks ?? [];
  const track = tracks.find((t) => t.track_id === selected) ?? null;
  const std = track ? positionStd(track) : null;
  const zones = scene?.geofences ?? [];
  const nearest =
    track && zones.length
      ? zones
          .map((zone) => ({
            name: zone.name,
            distance: centerZoneClearance(track.position, zone),
          }))
          .sort((a, b) => Math.abs(a.distance) - Math.abs(b.distance))[0]
      : null;
  const speed = track ? Math.hypot(...track.velocity) : null;
  return (
    <aside
      className={`inspector dock ${selected !== null ? "has-selection" : ""}`}
      aria-label="Entity inspector"
    >
      <div className="section-title">
        Selected entity{" "}
        <span className="muted">{track ? `#${track.track_id}` : "—"}</span>
        {selected !== null && (
          <button
            className="icon-button"
            aria-label="Clear selected entity"
            onClick={onClear}
          >
            <Icon name="close" size={14} />
          </button>
        )}
      </div>
      {track ? (
        <>
          <div className="entity-heading">
            <div
              className="entity-icon"
              style={{
                color: palette[track.semantic_class],
                background: `${palette[track.semantic_class]}12`,
                borderColor: `${palette[track.semantic_class]}65`,
              }}
            >
              <Icon
                name={track.semantic_class === 2 ? "person" : "cube"}
                size={27}
              />
            </div>
            <div>
              <h2>
                {semanticNames[track.semantic_class] ?? track.semantic_name}
              </h2>
              <span className="subtle">
                Tracked object · {metric(track.confidence * 100, 0)}% confidence
              </span>
            </div>
            <button
              className="icon-button"
              onClick={() => onFocus(track.track_id)}
              title="Focus on selected entity"
              aria-label="Focus on selected entity"
            >
              <Icon name="target" />
            </button>
          </div>
          <div className="tabs small" role="tablist">
            {["Details", "Descriptors", "Motion"].map((name) => (
              <button
                key={name}
                role="tab"
                aria-selected={name === tab}
                onClick={() => setTab(name)}
              >
                {name}
              </button>
            ))}
          </div>
          {tab === "Details" && (
            <div className="detail-content">
              <dl className="facts">
                <dt>Type</dt>
                <dd>{semanticNames[track.semantic_class]}</dd>
                <dt>Track state</dt>
                <dd
                  className={
                    track.state_kind === "predicted" ? "amber-text" : ""
                  }
                >
                  {track.state_kind === "predicted"
                    ? "Predicted · dashed box"
                    : track.state_kind === "measurement_updated"
                      ? "Measurement updated"
                      : "Not reported"}
                </dd>
                <dt>Last measurement age</dt>
                <dd>
                  {typeof track.measurement_age_seconds === "number" &&
                  Number.isFinite(track.measurement_age_seconds)
                    ? `${metric(track.measurement_age_seconds, 2)} s`
                    : "Not reported"}
                </dd>
                <dt>Status</dt>
                <dd
                  className={
                    track.breached_geofences?.length
                      ? "danger-text"
                      : "mint-text"
                  }
                >
                  {track.breached_geofences?.length
                    ? "Zone breach"
                    : "No reported breach"}
                </dd>
                <dt>Speed</dt>
                <dd>
                  {metric(speed)} <span className="muted">m/s</span>
                </dd>
                <dt>Time to contact</dt>
                <dd>
                  {track.ttc_seconds === null
                    ? "No predicted contact"
                    : `${metric(track.ttc_seconds, 2)} s`}
                </dd>
                <dt>World position</dt>
                <dd className="mono">
                  {track.position.map((v) => metric(v, 2)).join(", ")} m
                </dd>
                <dt>Position σ, XYZ</dt>
                <dd className="mono">
                  {std
                    ? `${std.map((v) => metric(v, 3)).join(", ")} m`
                    : "Not reported"}
                </dd>
                <dt>Zone distance</dt>
                <dd>
                  {nearest
                    ? `${metric(nearest.distance, 2)} m`
                    : "No zone available"}
                </dd>
              </dl>
              {nearest && (
                <p className="caption">
                  Center to “{nearest.name}” boundary; negative means inside.
                  This is not body clearance.
                </p>
              )}
              <div className="legend-line">
                <span>Planned path</span>
                <i className="line-swatch mint" />
              </div>
              <div className="legend-line">
                <span>Track prediction</span>
                <i className="line-swatch violet" />
              </div>
              <p className="caption">
                Dashed, dimmer boxes indicate predicted tracks. Measurement age
                uses source time; the viewport’s observation-age warning still
                applies to the full scene.
              </p>
            </div>
          )}
          {tab === "Descriptors" && (
            <div className="descriptors">
              <p className="caption">
                Local geometry measurements. These do not explain the neural
                network’s decision.
              </p>
              {descriptorNames.map((name, i) => (
                <div className="descriptor" key={name}>
                  <span>{name}</span>
                  <strong className="mono">
                    {track.eigen_invariants?.length === 10
                      ? metric(track.eigen_invariants[i], 4)
                      : "—"}
                  </strong>
                </div>
              ))}
            </div>
          )}
          {tab === "Motion" && (
            <div className="detail-content">
              <dl className="facts">
                <dt>Velocity, XYZ</dt>
                <dd className="mono">
                  {track.velocity.map((v) => metric(v, 2)).join(", ")} m/s
                </dd>
                <dt>Acceleration</dt>
                <dd className="mono">
                  {track.acceleration?.map((v) => metric(v, 2)).join(", ") ??
                    "Not reported"}
                </dd>
                <dt>Prediction points</dt>
                <dd>{track.predicted_trajectory?.length ?? 0}</dd>
                <dt>Observed members</dt>
                <dd>{track.member_count ?? "Not reported"}</dd>
              </dl>
              <p className="caption">
                Prediction is a motion estimate. It does not grant navigation
                clearance.
              </p>
            </div>
          )}
        </>
      ) : (
        <div className="inspector-empty">
          <Icon name="target" size={32} />
          <h2>Explore the scene</h2>
          <p>
            Select a track in the viewport or list to inspect its motion and
            geometry.
          </p>
        </div>
      )}
      <div className="section-title divider">
        Nearby entities <span className="count">{tracks.length}</span>
      </div>
      <div className="track-list">
        {tracks.slice(0, 40).map((entity: Track) => (
          <button
            key={entity.track_id}
            className={`track-row ${selected === entity.track_id ? "selected" : ""}`}
            onClick={() => onSelect(entity.track_id)}
          >
            <span
              className="track-symbol"
              style={{
                color: palette[entity.semantic_class],
                background: `${palette[entity.semantic_class]}10`,
              }}
            >
              <Icon
                name={entity.semantic_class === 2 ? "person" : "cube"}
                size={24}
              />
            </span>
            <span>
              <strong>
                {semanticNames[entity.semantic_class]}{" "}
                <span className="muted">#{entity.track_id}</span>
              </strong>
              <small>
                {entity.breached_geofences?.length
                  ? "Inside protected zone"
                  : `${metric(Math.hypot(...entity.velocity))} m/s`}
              </small>
            </span>
            <span
              className={
                entity.breached_geofences?.length ? "dot danger" : "dot mint"
              }
            />
          </button>
        ))}
        {!tracks.length && (
          <p className="caption inset">No tracked entities in this frame.</p>
        )}
      </div>
      <div className="section-title divider">
        Processing latency{" "}
        <span className="mono">
          {latencies.length ? `${metric(latencies.at(-1))} ms` : "—"}
        </span>
      </div>
      <div className="latency-chart">
        <Sparkline values={latencies} />
        <div className="chart-caption">
          <span>Actual frame measurements</span>
          <span>{latencies.length} samples</span>
        </div>
      </div>
    </aside>
  );
}
