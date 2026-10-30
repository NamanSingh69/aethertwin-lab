import type { RenderMetrics } from "../../scene/SpatialRenderer";
import type { Metadata } from "../../lib/types";
import { metric } from "../../lib/status";
import { Icon } from "../Icon";

export function PipelinePanel({
  scene,
  metrics,
  dropped,
  onStage,
  onClose,
}: {
  scene: Metadata | null;
  metrics: RenderMetrics | null;
  dropped: number;
  onStage: (name: string) => void;
  onClose: () => void;
}) {
  const stages = Object.entries(scene?.diagnostics.module_ms ?? {}).filter(
    ([, value]) => typeof value === "number",
  );
  const summary: Array<[string, string | number]> = [
    ["Processing", `${metric(scene?.diagnostics.processing_ms)} ms`],
    ["Resident memory", `${metric(scene?.diagnostics.memory_mb, 0)} MB`],
    ["Queue", scene?.diagnostics.queue_depth ?? "—"],
    ["Browser drops", dropped],
    ["GPU geometries", metrics?.geometries ?? "—"],
    ["Draw calls", metrics?.drawCalls ?? "—"],
    ["Scene update", `${metric(metrics?.updateMs)} ms`],
    ["Render submit", `${metric(metrics?.renderSubmitMs)} ms`],
    ["Frame p95", `${metric(metrics?.frameMsP95)} ms`],
    ["Drawing buffer", metrics?.drawingBuffer.join(" × ") ?? "—"],
  ];
  return (
    <section className="pipeline-panel">
      <div className="pipeline-title">
        <span>
          <Icon name="chart" size={17} />
          Processing pipeline <small>Measured this frame</small>
        </span>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="Close pipeline"
        >
          <Icon name="close" size={16} />
        </button>
      </div>
      <div className="pipeline-nodes">
        {stages.length ? (
          stages.map(([name, time], index) => (
            <button
              className="pipeline-node"
              key={name}
              onClick={() => onStage(name)}
            >
              <span className="node-number">
                {String(index + 1).padStart(2, "0")}
              </span>
              <strong>{name.replaceAll("_", " ")}</strong>
              <span className="node-time mono">{metric(time)} ms</span>
              <small>
                {scene?.diagnostics.module_shapes?.[name]
                  ? JSON.stringify(scene.diagnostics.module_shapes[name])
                  : "Tensor shape not reported"}
              </small>
            </button>
          ))
        ) : (
          <p className="caption">
            No per-stage measurements reported for this frame.
          </p>
        )}
      </div>
      <div className="pipeline-summary">
        {summary.map(([label, value]) => (
          <span key={label}>
            {label} <b>{value}</b>
          </span>
        ))}
      </div>
    </section>
  );
}
