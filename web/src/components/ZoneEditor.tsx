import { useEffect, useRef, useState } from "react";
import { useFocusTrap } from "../hooks/useFocusTrap";
import type { Bootstrap, Metadata, Vec3, Zone } from "../lib/types";
import { zoneYaw, validZone } from "../lib/geometry";
import { canControl } from "../lib/status";
import { Icon } from "./Icon";
export function ZoneEditor({
  scene,
  bootstrap,
  onApply,
  onClose,
}: {
  scene: Metadata | null;
  bootstrap: Bootstrap;
  onApply: (zones: Zone[], revision: number) => Promise<boolean>;
  onClose: () => void;
}) {
  const [zones, setZones] = useState<Zone[]>([]),
    [index, setIndex] = useState(0),
    [revision, setRevision] = useState(bootstrap.control_revision),
    [pending, setPending] = useState(false),
    [error, setError] = useState("");
  const dialog = useRef<HTMLElement>(null);
  const reset = () => {
    setZones(
      (scene?.geofences ?? []).map((z) => ({
        ...z,
        yaw: zoneYaw(z),
        center: [...z.center],
        half_extents: [...z.half_extents],
      })),
    );
    setRevision(bootstrap.control_revision);
    setIndex(0);
    setError("");
  };
  useEffect(() => {
    reset();
  }, []);
  useFocusTrap(dialog, onClose);
  const zone = zones[index];
  const edit = (patch: Partial<Zone>) =>
    setZones(zones.map((z, i) => (i === index ? { ...z, ...patch } : z)));
  const changeVector = (
    field: "center" | "half_extents",
    axis: number,
    value: number,
  ) => {
    if (!zone) return;
    const vector = [...zone[field]] as Vec3;
    vector[axis] = value;
    edit({ [field]: vector });
  };
  const apply = async () => {
    if (!zones.every(validZone)) {
      setError(
        "Every zone needs a name, finite coordinates and positive half extents.",
      );
      return;
    }
    setPending(true);
    try {
      if (await onApply(zones, revision)) onClose();
    } finally {
      setPending(false);
    }
  };
  return (
    <div className="modal-scrim">
      <section
        ref={dialog}
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="zone-heading"
      >
        <div className="modal-head">
          <div>
            <span className="eyebrow">TRANSACTIONAL EDITOR</span>
            <h2 id="zone-heading">Protected zones</h2>
          </div>
          <button
            className="icon-button"
            onClick={onClose}
            aria-label="Close zone editor"
          >
            <Icon name="close" />
          </button>
        </div>
        <p className="caption">
          Monitoring zones report intersection. A zone becomes a navigation
          keep-out only through a configured backend policy.
        </p>
        <div className="zone-editor">
          <div className="zone-names">
            {zones.map((z, i) => (
              <button
                className={i === index ? "active" : ""}
                key={i}
                onClick={() => setIndex(i)}
              >
                <Icon name="fence" />
                {z.name || "Unnamed zone"}
              </button>
            ))}
            <button
              onClick={() => {
                setZones([
                  ...zones,
                  {
                    name: `Zone ${zones.length + 1}`,
                    center: [0, 0, 0.9],
                    half_extents: [0.6, 0.6, 0.9],
                    yaw: 0,
                    severity_level: 2,
                  },
                ]);
                setIndex(zones.length);
              }}
            >
              + Add zone
            </button>
          </div>
          {zone ? (
            <div className="zone-fields">
              <label>
                Name
                <input
                  value={zone.name}
                  onChange={(e) => edit({ name: e.target.value })}
                  maxLength={64}
                />
              </label>
              {(["center", "half_extents"] as const).map((field) => (
                <fieldset key={field}>
                  <legend>
                    {field === "center" ? "Center (m)" : "Half extents (m)"}
                  </legend>
                  <div className="triple-input">
                    {["X", "Y", "Z"].map((axis, i) => (
                      <label key={axis}>
                        {axis}
                        <input
                          type="number"
                          step="0.05"
                          value={zone[field][i]}
                          onChange={(e) =>
                            changeVector(field, i, e.target.valueAsNumber)
                          }
                        />
                      </label>
                    ))}
                  </div>
                </fieldset>
              ))}
              <div className="two-input">
                <label>
                  Yaw (degrees)
                  <input
                    type="number"
                    step="5"
                    value={
                      Math.round((((zone.yaw ?? 0) * 180) / Math.PI) * 100) /
                      100
                    }
                    onChange={(e) =>
                      edit({ yaw: (e.target.valueAsNumber * Math.PI) / 180 })
                    }
                  />
                </label>
                <label>
                  Severity
                  <select
                    value={zone.severity_level}
                    onChange={(e) =>
                      edit({ severity_level: Number(e.target.value) })
                    }
                  >
                    <option value={1}>Warning</option>
                    <option value={2}>Critical</option>
                    <option value={3}>Critical · high</option>
                  </select>
                </label>
              </div>
              <button
                className="text-button danger-text"
                onClick={() => {
                  setZones(zones.filter((_, i) => i !== index));
                  setIndex(Math.max(0, index - 1));
                }}
              >
                Remove zone
              </button>
            </div>
          ) : (
            <div className="caption">Add a zone to begin.</div>
          )}
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <div className="modal-footer">
          <span className="caption">
            Base revision {revision} · changes apply together
          </span>
          <button className="button ghost" onClick={reset}>
            Reload current
          </button>
          <button
            className="button primary"
            disabled={pending || !canControl(bootstrap, "set_geofences")}
            onClick={() => void apply()}
          >
            {pending ? "Submitting…" : "Apply zones"}
          </button>
        </div>
      </section>
    </div>
  );
}
