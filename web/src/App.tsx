import { useCallback, useEffect, useRef, useState } from "react";
import { requestJSON } from "./lib/api";
import type { Layers, LayerName, Vec3, Zone } from "./lib/types";
import { parseViewParams } from "./lib/viewParams";
import {
  canControl,
  sensorWarnings,
  reportedSensorStatus,
  sourceLabel,
  planningDefaults,
} from "./lib/status";
import { parsePoint, pickHeight } from "./lib/geometry";
import { sourceAvailable, sourceOptions, sourcePayload } from "./lib/sources";
import { useStudioSession, type Notice } from "./hooks/useStudioSession";
import { useControlCommands } from "./hooks/useControlCommands";
import { useSpatialRenderer } from "./hooks/useSpatialRenderer";
import { Inspector } from "./components/Inspector";
import { ZoneEditor } from "./components/ZoneEditor";
import { CommissioningPanel } from "./components/CommissioningPanel";
import { TopBar } from "./components/studio/TopBar";
import { LayerDock, layerInfo } from "./components/studio/LayerDock";
import {
  PickBanner,
  SceneEmpty,
  ViewportFooter,
  MapProgressIndicator,
  QUALITY_OPTIONS,
  ViewportHeading,
  ViewportStats,
  ViewportWarnings,
} from "./components/studio/ViewportHud";
import { HelpOverlay } from "./components/studio/HelpOverlay";
import { useKeyboardShortcuts } from "./hooks/useKeyboardShortcuts";
import type { Quality } from "./scene/SpatialRenderer";
import { RoutePanel } from "./components/studio/RoutePanel";
import { PipelinePanel } from "./components/studio/PipelinePanel";
import { PlaybackBar } from "./components/studio/PlaybackBar";
import {
  EventsDrawer,
  LiveHealthPanel,
  LoginPanel,
  Toast,
} from "./components/studio/Overlays";

const moduleLayers: Record<string, LayerName> = {
  occupancy: "voxels",
  perception_tracking: "tracks",
  planning: "path",
  deskew: "voxels",
  pose_deskew: "voxels",
  localization: "map",
  rgbd: "rgbd",
};
const STALE_SECONDS = 3;
// Explainer links open the studio on one stage: `?layers=path,risk&view=top`.
const requestedView = parseViewParams(
  typeof window === "undefined" ? "" : window.location.search,
);
const QUALITY_KEY = "aethertwin.quality";

/** Saved render quality; storage can be unavailable (private mode, policy). */
function storedQuality(): Quality {
  try {
    const value = localStorage.getItem(QUALITY_KEY);
    return QUALITY_OPTIONS.find((option) => option.value === value)?.value ?? "auto";
  } catch {
    return "auto";
  }
}

function finitePoint(text: string) {
  try {
    parsePoint(text);
    return true;
  } catch {
    return false;
  }
}

export default function App() {
  const host = useRef<HTMLDivElement>(null);
  const [notice, setNotice] = useState<Notice>(null);
  const [layers, setLayers] = useState<Layers>(requestedView.layers),
    [selected, setSelected] = useState<number | null>(null),
    [mode, setMode] = useState("Explore"),
    [cutaway, setCutaway] = useState(true),
    [preset, setPreset] = useState<string>(requestedView.view ?? "isometric"),
    [graphOpen, setGraphOpen] = useState(false),
    [eventsOpen, setEventsOpen] = useState(false),
    [zonesOpen, setZonesOpen] = useState(false),
    [routeOpen, setRouteOpen] = useState(false),
    [leftOpen, setLeftOpen] = useState(false),
    [haloOpen, setHaloOpen] = useState(true),
    [helpOpen, setHelpOpen] = useState(false),
    [quality, setQualityState] = useState<Quality>(storedQuality);
  const setQuality = (value: Quality) => {
    setQualityState(value);
    try {
      localStorage.setItem(QUALITY_KEY, value);
    } catch {
      // The choice still applies for this session.
    }
  };
  const [start, setStart] = useState(""),
    [goal, setGoal] = useState(""),
    [pickPlaneText, setPickPlaneText] = useState(""),
    [pickMode, setPickMode] = useState<"start" | "goal" | null>(null);
  const draftContext = useRef("");

  const session = useStudioSession({
    // `view` is assigned below; frames only arrive after the first render.
    onFrame: (frame) => view.renderer.current?.updateFrame(frame),
    setNotice,
  });
  const { bootstrap, scene, latest, age, connection } = session;
  const activePickHeight = pickMode
    ? pickHeight(pickMode === "start" ? start : goal, pickPlaneText)
    : null;
  const worldPick = useCallback((point: Vec3, target: "start" | "goal") => {
    const value = point.map((v) => v.toFixed(3)).join(", ");
    if (target === "start") setStart(value);
    else setGoal(value);
    setPickMode(null);
  }, []);
  const view = useSpatialRenderer(
    host,
    latest,
    {
      layers,
      selected,
      cutaway,
      pickMode,
      activePickHeight,
      mapManifestUrl: bootstrap?.map_manifest_url,
      mapRevision: bootstrap?.map_revision,
      appearanceUrl: bootstrap?.appearance_url,
      quality,
    },
    {
      onSelect: setSelected,
      onWorldPick: worldPick,
      setNotice,
      setError: session.setError,
    },
  );
  // Apply a requested camera view once, when the renderer reports its first frame.
  const viewApplied = useRef(false);
  useEffect(() => {
    if (viewApplied.current || !view.metrics || !requestedView.view) return;
    viewApplied.current = true;
    view.renderer.current?.preset(requestedView.view);
  }, [view.metrics, view.renderer]);
  const { busy, command } = useControlCommands({
    bootstrap,
    setBootstrap: session.setBootstrap,
    setNotice,
    refresh: session.refresh,
    expectAcknowledgement: session.expectAcknowledgement,
  });

  // Endpoint drafts belong to one source and map revision; simulation/replay
  // pre-fill from the reported robot position and current path goal.
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
  }, [scene, bootstrap?.source_mode, bootstrap?.map_revision, latest]);

  const login = async (token: string) => {
    try {
      await requestJSON("/api/v2/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      session.retry();
      return true;
    } catch (cause) {
      setNotice({ text: String(cause), bad: true });
      return false;
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

  const source = sourceLabel(scene, bootstrap),
    freshness = { ageSeconds: age, transport: connection },
    warnings = sensorWarnings(scene, freshness),
    paused = Boolean(bootstrap?.paused ?? scene?.diagnostics.paused),
    stale = age !== null && age > STALE_SECONDS;
  const options = sourceOptions(bootstrap);
  const sourceDisabled =
    busy || !canControl(bootstrap, "select_source") || !options.length;
  const chooseSource = (option: (typeof options)[number]) =>
    void command("select_source", sourcePayload(option));
  const liveAvailable = sourceAvailable(bootstrap, "live");
  const frameHalo = latest.current?.scene.halo ?? scene?.diagnostics.halo;
  const halo =
    bootstrap?.halo && session.bootstrapIsNewer()
      ? bootstrap.halo
      : (frameHalo ?? bootstrap?.halo);
  const calibration =
    scene?.identity?.calibration_id ??
    (typeof bootstrap?.identity === "object" &&
    typeof bootstrap.identity.calibration_id === "string"
      ? bootstrap.identity.calibration_id
      : null);
  const canSeek = canControl(bootstrap, "seek") && source === "Replay";
  const replay = bootstrap?.replay;
  const currentFrame =
    scene?.diagnostics.playback?.frame_index ??
    replay?.current_frame ??
    scene?.frame_id ??
    0;
  const hasPath =
    (latest.current?.arrays.path?.length ?? 0) > 3 &&
    ["ok", "success"].includes(scene?.trajectory_status ?? "");
  const seek = (frame: number) => void command("seek", { frame_index: frame });
  const chooseMode = (tab: string) => {
    setMode(tab);
    if (tab === "Live" && bootstrap?.source_mode !== "live" && liveAvailable)
      setNotice({
        text: "Choose the live source explicitly in the source menu. Acquisition failures remain visible.",
        bad: false,
      });
  };
  const closeRoute = () => {
    setRouteOpen(false);
    setPickMode(null);
  };
  const choosePreset = (value: string) => {
    setPreset(value);
    view.renderer.current?.preset(value);
  };
  // Shortcuts reuse the same handlers, role checks and command plane as the controls.
  useKeyboardShortcuts((key) => {
    if (key === "?") {
      setHelpOpen(true);
      return true;
    }
    if (key === "Escape") {
      if (pickMode) setPickMode(null);
      else if (eventsOpen) setEventsOpen(false);
      else if (routeOpen) closeRoute();
      else if (graphOpen) setGraphOpen(false);
      else if (selected !== null) setSelected(null);
      else return false;
      return true;
    }
    if (key === " ") {
      const action = paused ? "resume" : "pause";
      if (busy || !canControl(bootstrap, action)) return false;
      void command(action);
      return true;
    }
    if (/^[1-9]$/.test(key)) {
      const layer = layerInfo[Number(key) - 1];
      if (!layer) return false;
      setLayers((v) => ({ ...v, [layer.id]: !v[layer.id] }));
      return true;
    }
    switch (key.toLowerCase()) {
      case "c":
        setCutaway((v) => !v);
        return true;
      case "f":
        if (selected === null) return false;
        view.renderer.current?.focusTrack(selected);
        return true;
      case "i":
        choosePreset("isometric");
        return true;
      case "t":
        choosePreset("top");
        return true;
      case "s":
        choosePreset("sensor");
        return true;
      case "q": {
        const order = QUALITY_OPTIONS.map((option) => option.value);
        setQuality(order[(order.indexOf(quality) + 1) % order.length]);
        return true;
      }
      case "r":
        if (routeOpen) closeRoute();
        else setRouteOpen(true);
        return true;
      case "e":
        setEventsOpen((v) => !v);
        return true;
    }
    return false;
  });

  return (
    <div className="app-shell">
      <TopBar
        source={source}
        options={options}
        sourceDisabled={sourceDisabled}
        onChooseSource={chooseSource}
        mode={mode}
        onMode={chooseMode}
        connection={connection}
        paused={paused}
        stale={stale}
        hardwareStatus={bootstrap?.hardware_status}
        role={bootstrap?.role}
        onToggleDock={() => setLeftOpen((v) => !v)}
      />
      <LayerDock
        open={leftOpen}
        mode={mode}
        onMode={(tab) => {
          chooseMode(tab);
          setLeftOpen(false);
        }}
        source={source}
        role={bootstrap?.role}
        options={options}
        sourceDisabled={sourceDisabled}
        onChooseSource={chooseSource}
        tracks={scene?.tracks ?? []}
        selected={selected}
        onSelectTrack={(id) => {
          setSelected(id);
          setLeftOpen(false);
        }}
        lidarStatus={reportedSensorStatus(scene, "lidar", freshness)}
        depthStatus={reportedSensorStatus(scene, "depth", freshness)}
        halo={halo}
        haloOpen={haloOpen}
        onToggleHalo={() => setHaloOpen((value) => !value)}
        warning={warnings[0]}
        layers={layers}
        onLayer={(name, visible) => setLayers((v) => ({ ...v, [name]: visible }))}
        cutaway={cutaway}
        onCutaway={setCutaway}
        routeOpen={routeOpen}
        onToggleRoute={() => setRouteOpen((v) => !v)}
        zonesDisabled={!bootstrap}
        zoneCount={scene?.geofences?.length ?? 0}
        onOpenZones={() => setZonesOpen(true)}
        graphOpen={graphOpen}
        onToggleGraph={() => setGraphOpen((v) => !v)}
        mapRevision={bootstrap?.map_revision}
        capture={view.appearance}
      />
      <main className="spatial-main">
        <div className="viewport" ref={host} />
        <div className="viewport-vignette" aria-hidden="true" />
        <ViewportHeading
          mode={mode}
          preset={preset}
          onPreset={choosePreset}
          quality={quality}
          onQuality={setQuality}
          effects={view.metrics?.effects}
          onHelp={() => setHelpOpen(true)}
        />
        <ViewportStats
          scene={scene}
          age={age}
          stale={stale}
          metrics={view.metrics}
        />
        {!scene && <SceneEmpty error={session.error} onRetry={session.retry} />}
        <ViewportWarnings
          warnings={warnings}
          sourceError={bootstrap?.error}
          sourceMode={bootstrap?.source_mode}
          source={source}
          hasScene={Boolean(scene)}
          staleSeconds={stale && !paused ? age : null}
        />
        <MapProgressIndicator progress={view.mapProgress} />
        {pickMode && (
          <PickBanner
            target={pickMode}
            height={activePickHeight}
            onCancel={() => setPickMode(null)}
          />
        )}
        {routeOpen && (
          <RoutePanel
            start={start}
            goal={goal}
            onStart={setStart}
            onGoal={setGoal}
            pickPlane={pickPlaneText}
            onPickPlane={setPickPlaneText}
            pickMode={pickMode}
            onPick={setPickMode}
            canPlan={
              !busy &&
              finitePoint(start) &&
              finitePoint(goal) &&
              canControl(bootstrap, "set_start_goal")
            }
            onPlan={() => void requestRoute()}
            hasPath={hasPath}
            reason={scene?.diagnostics.trajectory_reason}
            onClose={closeRoute}
          />
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
          <LiveHealthPanel
            source={source}
            liveAvailable={liveAvailable}
            onReturn={() => setMode("Explore")}
          />
        )}
        <ViewportFooter />
        {graphOpen && (
          <PipelinePanel
            scene={scene}
            metrics={view.metrics}
            dropped={session.stream.current?.dropped ?? 0}
            onStage={(name) => {
              const layer = moduleLayers[name];
              if (layer) setLayers((v) => ({ ...v, [layer]: true }));
            }}
            onClose={() => setGraphOpen(false)}
          />
        )}
      </main>
      <Inspector
        scene={scene}
        selected={selected}
        onSelect={setSelected}
        onClear={() => setSelected(null)}
        onFocus={(id) => view.renderer.current?.focusTrack(id)}
        latencies={session.latencies}
      />
      <PlaybackBar
        source={source}
        timestamp={scene?.timestamp}
        paused={paused}
        canToggle={!busy && canControl(bootstrap, paused ? "resume" : "pause")}
        canStep={!busy && paused && canControl(bootstrap, "step")}
        onToggle={() => void command(paused ? "resume" : "pause")}
        onStep={() => void command("step")}
        events={session.events}
        frameCount={replay?.frame_count}
        currentFrame={currentFrame}
        canSeek={canSeek}
        busy={busy}
        onSeek={seek}
        eventsOpen={eventsOpen}
        onToggleEvents={() => setEventsOpen((v) => !v)}
        processingFps={scene?.diagnostics.end_to_end_fps ?? scene?.fps}
      />
      {eventsOpen && (
        <EventsDrawer
          events={session.events}
          canSeek={canSeek}
          onSeek={seek}
          onClose={() => setEventsOpen(false)}
        />
      )}
      {notice && <Toast notice={notice} onDismiss={() => setNotice(null)} />}
      {helpOpen && <HelpOverlay onClose={() => setHelpOpen(false)} />}
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
      {session.error && !bootstrap && <LoginPanel onLogin={login} />}
    </div>
  );
}
