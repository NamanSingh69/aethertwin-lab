import { useCallback, useEffect, useRef, useState } from "react";
import type { SpatialRenderer, RenderMetrics } from "./scene/SpatialRenderer";
import { jsonFrame } from "./scene/protocol";
import {
  SceneStream,
  requestJSON,
  sendCommand,
  type Connection,
} from "./lib/api";
import type {
  Bootstrap,
  Layers,
  LayerName,
  Metadata,
  SceneFrame,
  Vec3,
  Zone,
} from "./lib/types";
import { initialLayers } from "./lib/types";
import {
  canControl,
  formatDuration,
  metric,
  observationIdentity,
  sensorWarnings,
  reportedSensorStatus,
  sourceLabel,
  metadataOnly,
  planningDefaults,
} from "./lib/status";
import { parsePoint, pickHeight } from "./lib/geometry";
import { Icon } from "./components/Icon";
import { Inspector } from "./components/Inspector";
import { ZoneEditor } from "./components/ZoneEditor";
import { HaloMirror } from "./components/HaloMirror";
import { CommissioningPanel } from "./components/CommissioningPanel";

type EventEntry = {
  id: string;
  text: string;
  frame: number;
  time: number;
  kind?: "command";
};
type ControlStatus = {
  pending: number;
  history: Array<{
    request_id: string;
    action: string;
    status: string;
    reason?: string;
    at_unix: number;
  }>;
};
const layerInfo: Array<{
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
];
const moduleLayers: Record<string, LayerName> = {
  occupancy: "voxels",
  perception_tracking: "tracks",
  planning: "path",
  deskew: "voxels",
  pose_deskew: "voxels",
  localization: "map",
  rgbd: "rgbd",
};
export default function App() {
  const host = useRef<HTMLDivElement>(null),
    renderer = useRef<SpatialRenderer | null>(null),
    latest = useRef<SceneFrame | null>(null),
    stream = useRef<SceneStream | null>(null),
    lastIdentity = useRef(""),
    lastObserved = useRef(0),
    lastUI = useRef(0);
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null),
    [scene, setScene] = useState<Metadata | null>(null),
    [error, setError] = useState(""),
    [connection, setConnection] = useState<Connection>("connecting"),
    [notice, setNotice] = useState<{ text: string; bad: boolean } | null>(null),
    [busy, setBusy] = useState(false);
  const [layers, setLayers] = useState<Layers>(initialLayers),
    [selected, setSelected] = useState<number | null>(null),
    [mode, setMode] = useState("Explore"),
    [cutaway, setCutaway] = useState(true),
    [preset, setPreset] = useState("isometric"),
    [metrics, setMetrics] = useState<RenderMetrics | null>(null),
    [latencies, setLatencies] = useState<number[]>([]),
    [age, setAge] = useState<number | null>(null),
    [events, setEvents] = useState<EventEntry[]>([]),
    [graphOpen, setGraphOpen] = useState(false),
    [eventsOpen, setEventsOpen] = useState(false),
    [zonesOpen, setZonesOpen] = useState(false),
    [routeOpen, setRouteOpen] = useState(false),
    [leftOpen, setLeftOpen] = useState(false);
  const [haloOpen, setHaloOpen] = useState(true);
  const [start, setStart] = useState(""),
    [goal, setGoal] = useState(""),
    [pickPlaneText, setPickPlaneText] = useState(""),
    [pickMode, setPickMode] = useState<"start" | "goal" | null>(null),
    [seek, setSeek] = useState<number | null>(null),
    [token, setToken] = useState(""),
    [loginBusy, setLoginBusy] = useState(false),
    [retry, setRetry] = useState(0);
  const seenAlerts = useRef(new Set<string>()),
    eventsRef = useRef<EventEntry[]>([]);
  const pendingRequest = useRef<string | null>(null),
    seenCommands = useRef(new Set<string>());
  const bootstrapReceived = useRef(0);
  const draftContext = useRef("");
  const activePickHeight = pickMode
    ? pickHeight(pickMode === "start" ? start : goal, pickPlaneText)
    : null;
  let validPlan = false;
  try {
    parsePoint(start);
    parsePoint(goal);
    validPlan = true;
  } catch {
    /* Explicit finite endpoints are required. */
  }
  const rendererSettings = useRef({
    layers,
    selected,
    cutaway,
    pickMode,
    bootstrap,
    activePickHeight,
  });
  rendererSettings.current = {
    layers,
    selected,
    cutaway,
    pickMode,
    bootstrap,
    activePickHeight,
  };
  const installBootstrap = useCallback((updated: Bootstrap) => {
    bootstrapReceived.current = Date.now();
    setBootstrap({ ...updated, latest: null });
  }, []);
  const worldPick = useCallback((point: Vec3, target: "start" | "goal") => {
    const value = point.map((v) => v.toFixed(3)).join(", ");
    if (target === "start") setStart(value);
    else setGoal(value);
    setPickMode(null);
  }, []);
  const recordControls = useCallback((status: ControlStatus) => {
    for (const entry of status.history ?? []) {
      if (entry.request_id === pendingRequest.current) {
        setNotice({
          text:
            entry.status === "applied"
              ? `Applied ${entry.action.replaceAll("_", " ")}.`
              : `${entry.action.replaceAll("_", " ")} failed: ${entry.reason ?? "No reason reported"}`,
          bad: entry.status !== "applied",
        });
        pendingRequest.current = null;
      }
      if (seenCommands.current.has(entry.request_id)) continue;
      seenCommands.current.add(entry.request_id);
      eventsRef.current = [
        ...eventsRef.current.slice(-63),
        {
          id: `command-${entry.request_id}`,
          text: `${entry.action.replaceAll("_", " ")} ${entry.status}${entry.reason ? `: ${entry.reason}` : ""}`,
          frame: 0,
          time: entry.at_unix,
          kind: "command",
        },
      ];
      setEvents(eventsRef.current);
    }
    if (seenCommands.current.size > 256)
      seenCommands.current = new Set(
        status.history.map((entry) => entry.request_id),
      );
  }, []);
  useEffect(() => {
    if (!host.current) return;
    let disposed = false;
    const element = host.current;
    const failed = (event: Event) =>
      setNotice({
        text: `Map layer unavailable: ${(event as CustomEvent).detail}`,
        bad: true,
      });
    element.addEventListener("asseterror", failed);
    void import("./scene/SpatialRenderer")
      .then(({ SpatialRenderer }) => {
        if (disposed) return;
        const instance = new SpatialRenderer(
          element,
          setSelected,
          worldPick,
          setMetrics,
        );
        renderer.current = instance;
        const settings = rendererSettings.current;
        instance.setLayers(settings.layers);
        instance.setCutaway(settings.cutaway);
        instance.setSelected(settings.selected);
        instance.setPickMode(settings.pickMode, settings.activePickHeight);
        if (latest.current) instance.updateFrame(latest.current);
        if (settings.bootstrap?.map_manifest_url)
          void instance
            .loadManifest(settings.bootstrap.map_manifest_url)
            .catch((failedCause) =>
              setNotice({
                text: `Measured map: ${String(failedCause)}`,
                bad: true,
              }),
            );
      })
      .catch((cause) => {
        if (disposed) return;
        setError(`3D rendering could not start: ${String(cause)}`);
      });
    return () => {
      disposed = true;
      element.removeEventListener("asseterror", failed);
      renderer.current?.dispose();
      renderer.current = null;
    };
  }, [worldPick]);
  const acceptFrame = useCallback((frame: SceneFrame) => {
    latest.current = frame;
    try {
      renderer.current?.updateFrame(frame);
    } catch (cause) {
      setNotice({
        text: `Scene presentation failed: ${String(cause)}`,
        bad: true,
      });
    }
    const key = observationIdentity(
      frame.scene,
      frame.streamId === "json" ? "snapshot" : frame.streamId,
    );
    if (key !== lastIdentity.current) {
      lastIdentity.current = key;
      const processed = frame.scene.processed_at_unix;
      lastObserved.current =
        typeof processed === "number" &&
        Number.isFinite(processed) &&
        processed > 0
          ? Math.min(Date.now(), processed * 1000)
          : Date.now();
      const ms = frame.scene.diagnostics.processing_ms;
      if (typeof ms === "number" && Number.isFinite(ms))
        setLatencies((values) => [...values.slice(-99), ms]);
    }
    for (const text of frame.scene.alerts ?? []) {
      const id = `${frame.streamId}:${text}`;
      if (!seenAlerts.current.has(id)) {
        seenAlerts.current.add(id);
        eventsRef.current = [
          ...eventsRef.current.slice(-63),
          {
            id,
            text,
            frame: frame.scene.frame_id,
            time: frame.scene.timestamp,
          },
        ];
        if (seenAlerts.current.size > 256)
          seenAlerts.current = new Set(eventsRef.current.map((e) => e.id));
        setEvents(eventsRef.current);
      }
    }
    if (performance.now() - lastUI.current > 150) {
      setScene(metadataOnly(frame.scene));
      lastUI.current = performance.now();
    }
  }, []);
  useEffect(() => {
    let disposed = false;
    let poll: ReturnType<typeof setInterval> | undefined;
    setError("");
    void requestJSON<Bootstrap>("/api/v2/bootstrap")
      .then((data) => {
        if (disposed) return;
        installBootstrap(data);
        if (data.latest) acceptFrame(jsonFrame(data.latest));
        stream.current = new SceneStream(
          data.stream_url ?? "/ws/v2/scene",
          acceptFrame,
          (status, message) => {
            setConnection(status);
            if (message && status === "offline")
              setNotice({ text: message, bad: true });
          },
        );
        poll = setInterval(() => {
          void requestJSON<Bootstrap>("/api/v2/bootstrap?include_latest=false")
            .then((updated) => {
              if (!disposed) installBootstrap(updated);
            })
            .catch(() => {});
          void requestJSON<ControlStatus>("/api/v2/commands")
            .then((status) => {
              if (!disposed) recordControls(status);
            })
            .catch(() => {});
        }, 4000);
      })
      .catch((cause) => {
        if (!disposed) {
          setError(String(cause));
          setConnection("offline");
        }
      });
    return () => {
      disposed = true;
      stream.current?.close();
      stream.current = null;
      if (poll) clearInterval(poll);
    };
  }, [acceptFrame, retry, recordControls, installBootstrap]);
  useEffect(() => {
    if (bootstrap?.map_manifest_url)
      void renderer.current
        ?.loadManifest(bootstrap.map_manifest_url)
        .catch((cause) =>
          setNotice({ text: `Measured map: ${String(cause)}`, bad: true }),
        );
  }, [bootstrap?.map_revision, bootstrap?.map_manifest_url]);
  useEffect(() => {
    renderer.current?.setLayers(layers);
  }, [layers]);
  useEffect(() => {
    renderer.current?.setCutaway(cutaway);
  }, [cutaway]);
  useEffect(() => {
    renderer.current?.setSelected(selected);
  }, [selected]);
  useEffect(() => {
    renderer.current?.setPickMode(pickMode, activePickHeight);
  }, [pickMode, activePickHeight]);
  useEffect(() => {
    const timer = setInterval(
      () =>
        setAge(
          lastObserved.current
            ? Math.max(0, (Date.now() - lastObserved.current) / 1000)
            : null,
        ),
      250,
    );
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    const currentMode =
      bootstrap?.source_mode ??
      scene?.source_mode ??
      scene?.diagnostics.source_mode;
    if (!currentMode || (!scene && currentMode !== "live")) return;
    const context = `${currentMode}:${bootstrap?.map_revision ?? scene?.identity?.map_revision ?? ""}`;
    if (draftContext.current === context) return;
    draftContext.current = context;
    setStart("");
    setGoal("");
    setPickPlaneText("");
    setPickMode(null);
    if (!scene) return;
    const defaults = planningDefaults(
      scene,
      latest.current?.arrays.path,
      currentMode,
    );
    if (defaults.start)
      setStart(defaults.start.map((value) => value.toFixed(3)).join(", "));
    if (defaults.goal)
      setGoal(defaults.goal.map((value) => value.toFixed(3)).join(", "));
  }, [scene, bootstrap?.source_mode, bootstrap?.map_revision]);
  const command = async (
    action: string,
    payload: unknown = {},
    revision?: number,
  ) => {
    if (!bootstrap || !canControl(bootstrap, action)) {
      setNotice({
        text: "This session or source does not support that control.",
        bad: true,
      });
      return false;
    }
    setBusy(true);
    try {
      const ack = await sendCommand(
        bootstrap,
        action,
        payload,
        revision ?? bootstrap.control_revision,
      );
      setBootstrap((b) => (b ? { ...b, control_revision: ack.revision } : b));
      setNotice({
        text:
          ack.status === "accepted"
            ? `Queued ${action.replaceAll("_", " ")}. Waiting for the processing boundary.`
            : (ack.reason ?? "Request rejected."),
        bad: ack.status !== "accepted",
      });
      if (ack.status === "accepted") pendingRequest.current = ack.request_id;
      if (ack.status === "accepted")
        setTimeout(() => {
          void requestJSON<Bootstrap>("/api/v2/bootstrap?include_latest=false")
            .then(installBootstrap)
            .catch(() => {});
          void requestJSON<ControlStatus>("/api/v2/commands")
            .then(recordControls)
            .catch(() => {});
        }, 700);
      return ack.status === "accepted";
    } catch (cause) {
      setNotice({ text: String(cause), bad: true });
      return false;
    } finally {
      setBusy(false);
    }
  };
  const requestRoute = async () => {
    try {
      await command("set_start_goal", {
        start: parsePoint(start),
        goal: parsePoint(goal),
      });
    } catch (cause) {
      setNotice({ text: String(cause), bad: true });
    }
  };
  const login = async () => {
    setLoginBusy(true);
    try {
      await requestJSON("/api/v2/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      setToken("");
      setRetry((v) => v + 1);
    } catch (cause) {
      setNotice({ text: String(cause), bad: true });
    } finally {
      setLoginBusy(false);
    }
  };
  const source = sourceLabel(scene, bootstrap),
    warnings = sensorWarnings(scene, {
      ageSeconds: age,
      transport: connection,
    }),
    paused = Boolean(bootstrap?.paused ?? scene?.diagnostics.paused),
    stale = age !== null && age > 3;
  const lidarStatus = reportedSensorStatus(scene, "lidar", {
    ageSeconds: age,
    transport: connection,
  });
  const depthStatus = reportedSensorStatus(scene, "depth", {
    ageSeconds: age,
    transport: connection,
  });
  const sourceModes = bootstrap?.available_sources ?? [];
  const frameHalo = latest.current?.scene.halo ?? scene?.diagnostics.halo;
  const halo =
    bootstrap?.halo &&
    bootstrapReceived.current > (latest.current?.receivedAt ?? 0)
      ? bootstrap.halo
      : (frameHalo ?? bootstrap?.halo);
  const sourceOptions = sourceModes.map((value, index) =>
    typeof value === "string"
      ? {
          id: `source-${index}`,
          source: value,
          label: value,
          recording_id: undefined,
        }
      : {
          ...value,
          id: value.id ?? `source-${index}`,
          label: value.label ?? value.source,
        },
  );
  const calibration =
    scene?.identity?.calibration_id ??
    (typeof bootstrap?.identity === "object" &&
    typeof bootstrap.identity.calibration_id === "string"
      ? bootstrap.identity.calibration_id
      : null);
  const canSeek = canControl(bootstrap, "seek") && source === "Replay";
  const replay = bootstrap?.replay;
  const frameCount = replay?.frame_count;
  const currentFrame =
    scene?.diagnostics.playback?.frame_index ??
    replay?.current_frame ??
    scene?.frame_id ??
    0;
  const hasPath =
    (latest.current?.arrays.path?.length ?? 0) > 3 &&
    ["ok", "success"].includes(scene?.trajectory_status ?? "");
  const moduleEntries = Object.entries(
    scene?.diagnostics.module_ms ?? {},
  ).filter(([, value]) => typeof value === "number");
  const sourceAvailable = (target: string) =>
    sourceModes.some((s) => (typeof s === "string" ? s : s.source) === target);
  const chooseMode = (tab: string) => {
    setMode(tab);
    if (
      tab === "Live" &&
      bootstrap?.source_mode !== "live" &&
      sourceAvailable("live")
    )
      setNotice({
        text: "Choose the live source explicitly in the source menu. Acquisition failures remain visible.",
        bad: false,
      });
  };
  return (
    <div className="app-shell">
      <header className="topbar">
        <button
          className="mobile-menu icon-button"
          aria-label="Toggle layer dock"
          onClick={() => setLeftOpen((v) => !v)}
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
          <select
            value="__current"
            onChange={(e) => {
              const chosen = sourceOptions.find(
                (option) => option.id === e.target.value,
              );
              if (chosen)
                void command("select_source", {
                  source: chosen.source,
                  ...(chosen.recording_id
                    ? { recording_id: chosen.recording_id }
                    : {}),
                });
            }}
            disabled={
              busy ||
              !canControl(bootstrap, "select_source") ||
              !sourceModes.length
            }
          >
            <option value="__current">
              {source === "Simulation"
                ? "Demo data"
                : source === "Replay"
                  ? "Recorded data"
                  : source === "Live"
                    ? "Live sensors"
                    : "Choose source"}
            </option>
            {sourceOptions.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          <Icon name="down" size={14} />
        </label>
        <nav className="main-nav" aria-label="Workspace">
          {["Explore", "Live", "Replay", "Commission"].map((tab) => (
            <button
              className={mode === tab ? "active" : ""}
              key={tab}
              onClick={() => chooseMode(tab)}
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
            {connection === "streaming"
              ? paused
                ? "Paused"
                : stale
                  ? "Stale observations"
                  : "Connected"
              : connection === "fallback"
                ? "Snapshot fallback"
                : connection === "resync"
                  ? "Resynchronizing"
                  : connection === "offline"
                    ? "Disconnected"
                    : "Connecting"}
          </span>
          <span className="hardware-badge">
            <Icon name="link" size={15} />
            {bootstrap?.hardware_status === "verified"
              ? "Hardware verified"
              : "Hardware not tested"}
          </span>
          <span className="role-badge" title="Session role">
            {bootstrap?.role ?? "guest"}
          </span>
        </div>
      </header>
      <aside className={`layer-dock dock ${leftOpen ? "open" : ""}`}>
        <section className="responsive-workspace" aria-label="Workspace menu">
          <div className="section-title">Workspace</div>
          <nav aria-label="All workspaces">
            {["Explore", "Live", "Replay", "Commission"].map((tab) => (
              <button
                className={mode === tab ? "active" : ""}
                key={tab}
                onClick={() => {
                  chooseMode(tab);
                  setLeftOpen(false);
                }}
              >
                {tab}
              </button>
            ))}
          </nav>
          <label>
            Data source
            <select
              aria-label="Mobile data source"
              value="__current"
              disabled={
                busy ||
                !canControl(bootstrap, "select_source") ||
                !sourceOptions.length
              }
              onChange={(event) => {
                const chosen = sourceOptions.find(
                  (option) => option.id === event.target.value,
                );
                if (chosen)
                  void command("select_source", {
                    source: chosen.source,
                    ...(chosen.recording_id
                      ? { recording_id: chosen.recording_id }
                      : {}),
                  });
              }}
            >
              <option value="__current">{source}</option>
              {sourceOptions.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <label className="mobile-track-picker">
            Inspect an entity
            <select
              aria-label="Select tracked entity"
              value={selected ?? ""}
              onChange={(event) => {
                if (event.target.value) {
                  setSelected(Number(event.target.value));
                  setLeftOpen(false);
                }
              }}
            >
              <option value="">Select a track</option>
              {(scene?.tracks ?? []).slice(0, 256).map((track) => (
                <option key={track.track_id} value={track.track_id}>
                  {track.semantic_name.replaceAll("_", " ")} #{track.track_id}
                </option>
              ))}
            </select>
          </label>
          <p className="caption mobile-session-role">
            {bootstrap?.role ?? "Guest"} session · {source}
          </p>
        </section>
        <div className="section-title">
          Sensors <span className="eyebrow">{source.toUpperCase()}</span>
        </div>
        <div className="sensor-row">
          <Icon name="scan" />
          <span>3D LiDAR</span>
          <span
            className={`status-light ${lidarStatus.tone}`}
            title={lidarStatus.title}
            role="img"
            aria-label={lidarStatus.label}
          />
        </div>
        <div className="sensor-row">
          <Icon name="camera" />
          <span>Depth camera</span>
          <span
            className={`status-light ${depthStatus.tone}`}
            title={depthStatus.title}
            role="img"
            aria-label={depthStatus.label}
          />
        </div>
        <button
          className="sensor-row halo-toggle"
          aria-expanded={haloOpen}
          onClick={() => setHaloOpen((value) => !value)}
        >
          <Icon name="cube" />
          <span>Virtual halo</span>
          <span className="sensor-label">{halo?.state_name ?? "Optional"}</span>
        </button>
        {haloOpen && <HaloMirror halo={halo} />}
        <div className="sensor-note">
          <Icon name="alert" size={16} />
          <span>
            {warnings[0] ??
              (source === "Simulation"
                ? "Synthetic data · no hardware claim"
                : "Physical validation not reported")}
          </span>
        </div>
        <div className="section-title divider">
          Layers{" "}
          <span className="muted">
            {Object.values(layers).filter(Boolean).length}
          </span>
        </div>
        <div className="layer-list">
          {layerInfo.map((layer) => (
            <label className="layer-row" key={layer.id}>
              <input
                type="checkbox"
                checked={layers[layer.id]}
                onChange={(e) =>
                  setLayers((v) => ({ ...v, [layer.id]: e.target.checked }))
                }
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
        <label className="cutaway-switch">
          <span>Cutaway view</span>
          <input
            type="checkbox"
            role="switch"
            checked={cutaway}
            onChange={(e) => setCutaway(e.target.checked)}
          />
          <i />
        </label>
        <p className="caption layer-caption">
          Display layers only. All backend algorithms remain active.
        </p>
        <div className="section-title divider">Workspace tools</div>
        <button
          className={`tool-row ${routeOpen ? "active" : ""}`}
          onClick={() => setRouteOpen((v) => !v)}
        >
          <Icon name="route" />
          <span>Plan a safe route</span>
          <Icon name="arrow" size={14} />
        </button>
        <button
          className="tool-row"
          onClick={() => setZonesOpen(true)}
          disabled={!bootstrap}
        >
          <Icon name="fence" />
          <span>Protected zones</span>
          <span className="count">{scene?.geofences?.length ?? 0}</span>
        </button>
        <button
          className={`tool-row ${graphOpen ? "active" : ""}`}
          onClick={() => setGraphOpen((v) => !v)}
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
              {bootstrap?.map_revision?.slice(0, 12) ?? "unavailable"}
            </span>
          </div>
        </div>
      </aside>
      <main className="spatial-main">
        <div className="viewport" ref={host} />
        <div className="viewport-vignette" aria-hidden="true" />
        <div className="viewport-heading">
          <div>
            <span className="eyebrow">
              {mode === "Explore"
                ? "SPATIAL EXPLORER"
                : mode === "Commission"
                  ? "COMMISSIONING WORKSPACE"
                  : mode.toUpperCase() + " WORKSPACE"}
            </span>
            <h1>
              {mode === "Commission"
                ? "Know your reference."
                : mode === "Live"
                  ? "Live spatial awareness."
                  : "Your lab, in perspective."}
            </h1>
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
              <select
                value={preset}
                onChange={(e) => {
                  setPreset(e.target.value);
                  renderer.current?.preset(e.target.value);
                }}
              >
                <option value="isometric">Isometric</option>
                <option value="top">Top view</option>
                <option value="sensor">Sensor view</option>
              </select>
              <Icon name="down" size={14} />
            </label>
          </div>
        </div>
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
            Render{" "}
            <strong className="mono">{metric(metrics?.fps, 0)} fps</strong>
          </span>
        </div>
        {!scene && (
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
              {error ||
                "The measured map and sensor observations will appear here."}
            </p>
            {error && (
              <button
                className="button ghost"
                onClick={() => setRetry((v) => v + 1)}
              >
                Reconnect
              </button>
            )}
          </div>
        )}
        {warnings.length > 0 && (
          <div className="sensor-warning" role="status">
            <Icon name="alert" size={16} />
            <span>{warnings.join(" · ")}</span>
          </div>
        )}
        {bootstrap?.error && (
          <div className="sensor-warning source-failure" role="alert">
            <Icon name="alert" size={16} />
            <span>
              {bootstrap.source_mode} source unavailable: {bootstrap.error}.{" "}
              {scene
                ? `Last ${source.toLowerCase()} observation retained.`
                : "No observations available."}
            </span>
          </div>
        )}
        {stale && !paused && scene && (
          <div className="stale-warning">
            <Icon name="alert" size={16} />
            No new observation for {age?.toFixed(1)} s. Last scene remains
            visible.
          </div>
        )}
        {pickMode && (
          <div className="pick-banner">
            <Icon name="target" />
            Pick {pickMode} on world Z = {activePickHeight?.toFixed(3)} m.
            Backend validates occupied and unknown space.
            <button onClick={() => setPickMode(null)}>Cancel</button>
          </div>
        )}
        {routeOpen && (
          <section className="route-panel glass">
            <div className="section-title">
              Safe trajectory{" "}
              <button
                className="icon-button"
                onClick={() => {
                  setRouteOpen(false);
                  setPickMode(null);
                }}
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
                value={pickPlaneText}
                onChange={(event) => setPickPlaneText(event.target.value)}
              />
            </label>
            <div className="route-input">
              <label>
                Start · x, y, z (m)
                <input
                  placeholder="Choose start"
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                />
              </label>
              <button
                className={
                  pickMode === "start" ? "active icon-button" : "icon-button"
                }
                title="Pick start on floor"
                aria-label="Pick start on floor"
                onClick={() => setPickMode("start")}
                disabled={pickHeight(start, pickPlaneText) === null}
              >
                <Icon name="target" />
              </button>
            </div>
            <div className="route-input">
              <label>
                Goal · x, y, z (m)
                <input
                  placeholder="Choose goal"
                  value={goal}
                  onChange={(e) => setGoal(e.target.value)}
                />
              </label>
              <button
                className={
                  pickMode === "goal" ? "active icon-button" : "icon-button"
                }
                title="Pick goal on floor"
                aria-label="Pick goal on floor"
                onClick={() => setPickMode("goal")}
                disabled={pickHeight(goal, pickPlaneText) === null}
              >
                <Icon name="target" />
              </button>
            </div>
            <div className="route-actions">
              <button
                className="button primary"
                disabled={
                  busy || !validPlan || !canControl(bootstrap, "set_start_goal")
                }
                onClick={() => void requestRoute()}
              >
                <Icon name="route" size={16} />
                Plan route
              </button>
              <a
                className={`button ghost ${hasPath ? "" : "disabled"}`}
                aria-disabled={!hasPath}
                href={hasPath ? "/api/v2/trajectory.csv" : undefined}
                download="trajectory.csv"
              >
                <Icon name="download" size={15} />
                CSV
              </a>
            </div>
            <p className="caption">
              {scene?.diagnostics.trajectory_reason ||
                "Measured occupancy remains navigation authority. No physical actuation."}
            </p>
          </section>
        )}
        {mode === "Commission" && (
          <CommissioningPanel
            role={bootstrap?.role}
            csrfToken={bootstrap?.csrf_token}
            calibration={calibration}
            mapRevision={bootstrap?.map_revision}
            hardwareStatus={bootstrap?.hardware_status}
            onClose={() => setMode("Explore")}
          />
        )}
        {mode === "Live" && source !== "Live" && (
          <section className="commission-panel live-health glass">
            <span className="eyebrow">LIVE SENSOR HEALTH</span>
            <h2>Connect with confidence.</h2>
            <dl className="facts">
              <dt>Current observations</dt>
              <dd>{source}</dd>
              <dt>Physical validation</dt>
              <dd className="amber-text">Not tested</dd>
              <dt>Live source</dt>
              <dd>
                {sourceAvailable("live")
                  ? "Configured · select explicitly"
                  : "Not configured"}
              </dd>
            </dl>
            <p className="caption">
              This health view keeps the current acquisition source.{" "}
              {sourceAvailable("live")
                ? "Choose the live rig in the data source menu to request live acquisition."
                : "A measured twin and connected sensors are required before live acquisition can start."}
            </p>
            <button className="button ghost" onClick={() => setMode("Explore")}>
              Return to explorer <Icon name="arrow" size={15} />
            </button>
          </section>
        )}
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
        {graphOpen && (
          <section className="pipeline-panel">
            <div className="pipeline-title">
              <span>
                <Icon name="chart" size={17} />
                Processing pipeline <small>Measured this frame</small>
              </span>
              <button
                className="icon-button"
                onClick={() => setGraphOpen(false)}
                aria-label="Close pipeline"
              >
                <Icon name="close" size={16} />
              </button>
            </div>
            <div className="pipeline-nodes">
              {moduleEntries.length ? (
                moduleEntries.map(([name, time], index) => (
                  <button
                    className="pipeline-node"
                    key={name}
                    onClick={() => {
                      const layer = moduleLayers[name];
                      if (layer) setLayers((v) => ({ ...v, [layer]: true }));
                    }}
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
              <span>
                Processing <b>{metric(scene?.diagnostics.processing_ms)} ms</b>
              </span>
              <span>
                Resident memory{" "}
                <b>{metric(scene?.diagnostics.memory_mb, 0)} MB</b>
              </span>
              <span>
                Queue <b>{scene?.diagnostics.queue_depth ?? "—"}</b>
              </span>
              <span>
                Browser drops <b>{stream.current?.dropped ?? 0}</b>
              </span>
              <span>
                GPU geometries <b>{metrics?.geometries ?? "—"}</b>
              </span>
              <span>
                Draw calls <b>{metrics?.drawCalls ?? "—"}</b>
              </span>
              <span>
                Scene update <b>{metric(metrics?.updateMs)} ms</b>
              </span>
              <span>
                Render submit <b>{metric(metrics?.renderSubmitMs)} ms</b>
              </span>
              <span>
                Frame p95 <b>{metric(metrics?.frameMsP95)} ms</b>
              </span>
              <span>
                Drawing buffer{" "}
                <b>{metrics?.drawingBuffer.join(" × ") ?? "—"}</b>
              </span>
            </div>
          </section>
        )}
      </main>
      <Inspector
        scene={scene}
        selected={selected}
        onSelect={setSelected}
        onClear={() => setSelected(null)}
        onFocus={(id) => renderer.current?.focusTrack(id)}
        latencies={latencies}
      />
      <footer className="playback">
        <div className="playback-label">
          <span>{source === "Replay" ? "Replay" : "Session"}</span>
          <small className="mono">
            {scene ? formatDuration(scene.timestamp) : "—"}
          </small>
        </div>
        <div className="playback-controls">
          <button
            className="play-button"
            disabled={
              busy || !canControl(bootstrap, paused ? "resume" : "pause")
            }
            onClick={() => void command(paused ? "resume" : "pause")}
            aria-label={paused ? "Resume processing" : "Pause processing"}
          >
            <Icon name={paused ? "play" : "pause"} size={22} />
          </button>
          <button
            className="icon-button step-button"
            disabled={busy || !paused || !canControl(bootstrap, "step")}
            onClick={() => void command("step")}
            aria-label="Process one frame"
          >
            <Icon name="step" size={17} />
          </button>
        </div>
        <div className="timeline-group">
          <div className="timeline-events" aria-hidden="true">
            {events
              .filter((event) => event.kind !== "command")
              .slice(-30)
              .map((event, i) => (
                <i
                  key={event.id}
                  style={{
                    left: `${frameCount ? Math.min(100, (event.frame / frameCount) * 100) : ((i + 1) / Math.max(events.length, 1)) * 100}%`,
                    background: event.text.includes("CRITICAL")
                      ? "#ff687b"
                      : "#f1bf67",
                  }}
                />
              ))}
          </div>
          <input
            className="timeline"
            type="range"
            aria-label="Replay frame"
            min={0}
            max={Math.max(1, (frameCount ?? 1) - 1)}
            value={Math.min(
              seek ?? currentFrame,
              Math.max(1, (frameCount ?? 1) - 1),
            )}
            disabled={!canSeek || !frameCount || busy}
            onChange={(e) => setSeek(Number(e.target.value))}
            onPointerUp={() => {
              if (seek !== null) void command("seek", { frame_index: seek });
              setSeek(null);
            }}
            onKeyUp={(e) => {
              if (
                [
                  "ArrowLeft",
                  "ArrowRight",
                  "ArrowUp",
                  "ArrowDown",
                  "PageUp",
                  "PageDown",
                  "Home",
                  "End",
                ].includes(e.key) &&
                seek !== null
              ) {
                void command("seek", { frame_index: seek });
                setSeek(null);
              }
            }}
          />
          <div className="timeline-caption">
            <span>
              {canSeek
                ? "Seek a recorded frame"
                : "Frame seek is available for supported replay sources"}
            </span>
            <span className="mono">
              {frameCount
                ? `${currentFrame + 1} / ${frameCount} frames`
                : "Live progression"}
            </span>
          </div>
        </div>
        <button
          className={`event-button ${eventsOpen ? "active" : ""}`}
          onClick={() => setEventsOpen((v) => !v)}
        >
          <span className="dot amber" />
          {events.length} events
          <Icon name="down" size={14} />
        </button>
        <div className="session-rate">
          <strong className="mono">
            {metric(scene?.diagnostics.end_to_end_fps ?? scene?.fps)}
          </strong>
          <span>processing fps</span>
        </div>
      </footer>
      {eventsOpen && (
        <section className="events-drawer glass">
          <div className="section-title">
            Session events
            <button
              className="icon-button"
              onClick={() => setEventsOpen(false)}
              aria-label="Close events"
            >
              <Icon name="close" size={15} />
            </button>
          </div>
          {events.length ? (
            events
              .slice()
              .reverse()
              .map((event) => (
                <button
                  className="event-row"
                  key={event.id}
                  disabled={!canSeek || event.kind === "command"}
                  onClick={() =>
                    void command("seek", { frame_index: event.frame })
                  }
                >
                  <span className="mono">
                    {event.kind === "command" ? "Control" : `#${event.frame}`}
                  </span>
                  <span>{event.text}</span>
                </button>
              ))
          ) : (
            <p className="caption">No alerts reported in this session.</p>
          )}
        </section>
      )}
      {notice && (
        <div
          className={`toast ${notice.bad ? "error" : ""}`}
          role={notice.bad ? "alert" : "status"}
        >
          <Icon name={notice.bad ? "alert" : "check"} size={18} />
          <span>{notice.text}</span>
          <button
            className="icon-button"
            aria-label="Dismiss message"
            onClick={() => setNotice(null)}
          >
            <Icon name="close" size={14} />
          </button>
        </div>
      )}
      {zonesOpen && bootstrap && (
        <ZoneEditor
          scene={scene}
          bootstrap={bootstrap}
          onClose={() => setZonesOpen(false)}
          onApply={(zones: Zone[], revision: number) =>
            command(
              "set_geofences",
              {
                geofences: zones.map((z) => ({
                  name: z.name,
                  center: z.center,
                  half_extents: z.half_extents,
                  yaw: z.yaw ?? 0,
                  severity_level: z.severity_level,
                })),
              },
              revision,
            )
          }
        />
      )}
      {error && !bootstrap && (
        <section className="session-login glass">
          <h2>Connect to AetherTwin</h2>
          <p className="caption">
            Run the local service, or enter the access credential configured by
            its operator.
          </p>
          <label>
            Session credential
            <input
              type="password"
              autoComplete="current-password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void login();
              }}
            />
          </label>
          <button
            className="button primary"
            onClick={() => void login()}
            disabled={loginBusy || !token}
          >
            {loginBusy ? "Connecting…" : "Open session"}
          </button>
          <span className="caption">
            Credentials stay in an HTTP-only session cookie.
          </span>
        </section>
      )}
    </div>
  );
}
