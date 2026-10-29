import { pickHeight } from "../../lib/geometry";
import { Icon } from "../Icon";

type Target = "start" | "goal";

function EndpointInput({
  target,
  value,
  onChange,
  pickMode,
  pickPlane,
  onPick,
}: {
  target: Target;
  value: string;
  onChange: (value: string) => void;
  pickMode: Target | null;
  pickPlane: string;
  onPick: (target: Target) => void;
}) {
  const name = target === "start" ? "Start" : "Goal";
  return (
    <div className="route-input">
      <label>
        {name} · x, y, z (m)
        <input
          placeholder={`Choose ${target}`}
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      <button
        className={pickMode === target ? "active icon-button" : "icon-button"}
        title={`Pick ${target} on floor`}
        aria-label={`Pick ${target} on floor`}
        onClick={() => onPick(target)}
        disabled={pickHeight(value, pickPlane) === null}
      >
        <Icon name="target" />
      </button>
    </div>
  );
}

export function RoutePanel(props: {
  start: string;
  goal: string;
  onStart: (value: string) => void;
  onGoal: (value: string) => void;
  pickPlane: string;
  onPickPlane: (value: string) => void;
  pickMode: Target | null;
  onPick: (target: Target) => void;
  canPlan: boolean;
  onPlan: () => void;
  hasPath: boolean;
  reason: string | undefined;
  onClose: () => void;
}) {
  return (
    <section className="route-panel glass">
      <div className="section-title">
        Safe trajectory{" "}
        <button
          className="icon-button"
          onClick={props.onClose}
          aria-label="Close route panel"
        >
          <Icon name="close" size={15} />
        </button>
      </div>
      <label className="pick-height-input">
        Pick plane · world Z (m)
        <input
          type="number"
          step="0.05"
          placeholder="Enter height before picking"
          value={props.pickPlane}
          onChange={(event) => props.onPickPlane(event.target.value)}
        />
      </label>
      <EndpointInput
        target="start"
        value={props.start}
        onChange={props.onStart}
        pickMode={props.pickMode}
        pickPlane={props.pickPlane}
        onPick={props.onPick}
      />
      <EndpointInput
        target="goal"
        value={props.goal}
        onChange={props.onGoal}
        pickMode={props.pickMode}
        pickPlane={props.pickPlane}
        onPick={props.onPick}
      />
      <div className="route-actions">
        <button
          className="button primary"
          disabled={!props.canPlan}
          onClick={props.onPlan}
        >
          <Icon name="route" size={16} />
          Plan route
        </button>
        <a
          className={`button ghost ${props.hasPath ? "" : "disabled"}`}
          aria-disabled={!props.hasPath}
          href={props.hasPath ? "/api/v2/trajectory.csv" : undefined}
          download="trajectory.csv"
        >
          <Icon name="download" size={15} />
          CSV
        </a>
      </div>
      <p className="caption">
        {props.reason ||
          "Measured occupancy remains navigation authority. No physical actuation."}
      </p>
    </section>
  );
}
