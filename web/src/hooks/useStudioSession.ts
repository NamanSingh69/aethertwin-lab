import { useCallback, useEffect, useRef, useState } from "react";
import { jsonFrame } from "../scene/protocol";
import { SceneStream, requestJSON, type Connection } from "../lib/api";
import type { Bootstrap, Metadata, SceneFrame } from "../lib/types";
import { metadataOnly, observationIdentity } from "../lib/status";

export type EventEntry = {
  id: string;
  text: string;
  frame: number;
  time: number;
  kind?: "command";
};
export type ControlStatus = {
  pending: number;
  history: Array<{
    request_id: string;
    action: string;
    status: string;
    reason?: string;
    at_unix: number;
  }>;
};
export type Notice = { text: string; bad: boolean } | null;

const EVENT_LIMIT = 64;
const SEEN_LIMIT = 256;
const BOOTSTRAP_POLL_MS = 4000;
const SCENE_UI_INTERVAL_MS = 150;

/**
 * Owns the service session: bootstrap, the scene stream, periodic control and
 * bootstrap polling, alert/command event history and observation freshness.
 * Large typed arrays never enter React state; `onFrame` hands them to the renderer.
 */
export function useStudioSession({
  onFrame,
  setNotice,
}: {
  onFrame: (frame: SceneFrame) => void;
  setNotice: (notice: Notice) => void;
}) {
  const latest = useRef<SceneFrame | null>(null),
    stream = useRef<SceneStream | null>(null),
    lastIdentity = useRef(""),
    lastObserved = useRef(0),
    lastUI = useRef(0),
    bootstrapReceived = useRef(0),
    seenAlerts = useRef(new Set<string>()),
    seenCommands = useRef(new Set<string>()),
    eventsRef = useRef<EventEntry[]>([]),
    pendingRequest = useRef<string | null>(null);
  const onFrameRef = useRef(onFrame),
    noticeRef = useRef(setNotice);
  onFrameRef.current = onFrame;
  noticeRef.current = setNotice;
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null),
    [scene, setScene] = useState<Metadata | null>(null),
    [error, setError] = useState(""),
    [connection, setConnection] = useState<Connection>("connecting"),
    [latencies, setLatencies] = useState<number[]>([]),
    [age, setAge] = useState<number | null>(null),
    [events, setEvents] = useState<EventEntry[]>([]),
    [retryCount, setRetry] = useState(0);

  const pushEvent = useCallback((entry: EventEntry) => {
    eventsRef.current = [...eventsRef.current.slice(-(EVENT_LIMIT - 1)), entry];
  }, []);
  const installBootstrap = useCallback((updated: Bootstrap) => {
    bootstrapReceived.current = Date.now();
    setBootstrap({ ...updated, latest: null });
  }, []);
  const recordControls = useCallback(
    (status: ControlStatus) => {
      for (const entry of status.history ?? []) {
        if (entry.request_id === pendingRequest.current) {
          noticeRef.current({
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
        pushEvent({
          id: `command-${entry.request_id}`,
          text: `${entry.action.replaceAll("_", " ")} ${entry.status}${entry.reason ? `: ${entry.reason}` : ""}`,
          frame: 0,
          time: entry.at_unix,
          kind: "command",
        });
        setEvents(eventsRef.current);
      }
      if (seenCommands.current.size > SEEN_LIMIT)
        seenCommands.current = new Set(
          status.history.map((entry) => entry.request_id),
        );
    },
    [pushEvent],
  );
  const acceptFrame = useCallback(
    (frame: SceneFrame) => {
      latest.current = frame;
      try {
        onFrameRef.current(frame);
      } catch (cause) {
        noticeRef.current({
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
        if (seenAlerts.current.has(id)) continue;
        seenAlerts.current.add(id);
        pushEvent({
          id,
          text,
          frame: frame.scene.frame_id,
          time: frame.scene.timestamp,
        });
        if (seenAlerts.current.size > SEEN_LIMIT)
          seenAlerts.current = new Set(eventsRef.current.map((e) => e.id));
        setEvents(eventsRef.current);
      }
      if (performance.now() - lastUI.current > SCENE_UI_INTERVAL_MS) {
        setScene(metadataOnly(frame.scene));
        lastUI.current = performance.now();
      }
    },
    [pushEvent],
  );
  /** Refresh bootstrap (without the bulky latest scene) and control history. */
  const refresh = useCallback(() => {
    void requestJSON<Bootstrap>("/api/v2/bootstrap?include_latest=false")
      .then(installBootstrap)
      .catch(() => {});
    void requestJSON<ControlStatus>("/api/v2/commands")
      .then(recordControls)
      .catch(() => {});
  }, [installBootstrap, recordControls]);

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
              noticeRef.current({ text: message, bad: true });
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
        }, BOOTSTRAP_POLL_MS);
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
  }, [acceptFrame, retryCount, recordControls, installBootstrap]);

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

  const retry = useCallback(() => setRetry((v) => v + 1), []);
  /** Await a command acknowledgement in the next control-history poll. */
  const expectAcknowledgement = useCallback((requestId: string) => {
    pendingRequest.current = requestId;
  }, []);
  /** Bootstrap halo is newer than the latest frame's halo only if it arrived later. */
  const bootstrapIsNewer = () =>
    bootstrapReceived.current > (latest.current?.receivedAt ?? 0);

  return {
    bootstrap,
    setBootstrap,
    scene,
    latest,
    stream,
    error,
    setError,
    connection,
    latencies,
    age,
    events,
    retry,
    refresh,
    expectAcknowledgement,
    bootstrapIsNewer,
  };
}
