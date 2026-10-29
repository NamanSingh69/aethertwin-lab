import type { Connection } from "../../lib/api";
import type { SourceOption } from "../../lib/sources";
import { Icon } from "../Icon";
import { SourceSelect } from "./SourceSelect";

export const WORKSPACES = ["Explore", "Live", "Replay", "Commission"] as const;

function connectionText(connection: Connection, paused: boolean, stale: boolean) {
  if (connection === "streaming")
    return paused ? "Paused" : stale ? "Stale observations" : "Connected";
  return {
    fallback: "Snapshot fallback",
    resync: "Resynchronizing",
    offline: "Disconnected",
    connecting: "Connecting",
  }[connection];
}

function sourcePlaceholder(source: string) {
  return source === "Simulation"
    ? "Demo data"
    : source === "Replay"
      ? "Recorded data"
      : source === "Live"
        ? "Live sensors"
        : "Choose source";
}

export function TopBar({
  source,
  options,
  sourceDisabled,
  onChooseSource,
  mode,
  onMode,
  connection,
  paused,
  stale,
  hardwareStatus,
  role,
  onToggleDock,
}: {
  source: string;
  options: SourceOption[];
  sourceDisabled: boolean;
  onChooseSource: (option: SourceOption) => void;
  mode: string;
  onMode: (mode: string) => void;
  connection: Connection;
  paused: boolean;
  stale: boolean;
  hardwareStatus: string | undefined;
  role: string | undefined;
  onToggleDock: () => void;
}) {
  return (
    <header className="topbar">
      <button
        className="mobile-menu icon-button"
        aria-label="Toggle layer dock"
        onClick={onToggleDock}
      >
        <Icon name="layers" />
      </button>
      <a className="brand" href="#">
        <span className="brand-mark">
          <i />
          <i />
          <i />
        </span>
        Aether<span>Twin</span>
        <small>SPATIAL STUDIO</small>
      </a>
      <div className="header-divider" />
      <span
        className={`source-badge ${source === "Live" ? "live" : source === "Replay" ? "replay" : ""}`}
      >
        <span className="dot" />
        {source}
      </span>
      <label className="source-select">
        <span className="sr-only">Active data source</span>
        <SourceSelect
          options={options}
          currentLabel={sourcePlaceholder(source)}
          disabled={sourceDisabled}
          onChoose={onChooseSource}
        />
        <Icon name="down" size={14} />
      </label>
      <nav className="main-nav" aria-label="Workspace">
        {WORKSPACES.map((tab) => (
          <button
            className={mode === tab ? "active" : ""}
            key={tab}
            onClick={() => onMode(tab)}
          >
            {tab}
          </button>
        ))}
      </nav>
      <div className="header-status">
        <span className={`connection-pill ${stale ? "stale" : ""}`}>
          <span
            className={`dot ${connection === "streaming" && !stale ? "mint" : "amber"}`}
          />
          {connectionText(connection, paused, stale)}
        </span>
        <span className="hardware-badge">
          <Icon name="link" size={15} />
          {hardwareStatus === "verified"
            ? "Hardware verified"
            : "Hardware not tested"}
        </span>
        <span className="role-badge" title="Session role">
          {role ?? "guest"}
        </span>
      </div>
    </header>
  );
}
