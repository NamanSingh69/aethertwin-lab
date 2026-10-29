import { useCallback, useEffect, useRef, useState } from "react";
import { requestJSON } from "./lib/api";
import type { Layers, LayerName, Vec3, Zone } from "./lib/types";
import { initialLayers } from "./lib/types";
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
import { LayerDock } from "./components/studio/LayerDock";
import {
  PickBanner,
  SceneEmpty,
  ViewportFooter,
  ViewportHeading,
  ViewportStats,
  ViewportWarnings,
} from "./components/studio/ViewportHud";
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
  const [layers, setLayers] = useState<Layers>(initialLayers),
    [selected, setSelected] = useState<number | null>(null),
    [mode, setMode] = useState("Explore"),
    [cutaway, setCutaway] = useState(true),
    [preset, setPreset] = useState("isometric"),
    [graphOpen, setGraphOpen] = useState(false),
    [eventsOpen, setEventsOpen] = useState(false),
    [zonesOpen, setZonesOpen] = useState(false),
    [routeOpen, setRouteOpen] = useState(false),
    [leftOpen, setLeftOpen] = useState(false),
    [haloOpen, setHaloOpen] = useState(true);
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
    },
    {
      onSelect: setSelected,
      onWorldPick: worldPick,
      setNotice,
      setError: session.setError,
    },
  );
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
      />
      <main className="spatial-main">
        <div className="viewport" ref={host} />
        <div className="viewport-vignette" aria-hidden="true" />
        <ViewportHeading
          mode={mode}
          preset={preset}
          onPreset={(value) => {
            setPreset(value);
            view.renderer.current?.preset(value);
          }}
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
