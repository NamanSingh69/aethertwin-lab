import { useState } from "react";
import type { EventEntry } from "../../hooks/useStudioSession";
import { formatDuration, metric } from "../../lib/status";
import { Icon } from "../Icon";

const SEEK_KEYS = [
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "ArrowDown",
  "PageUp",
  "PageDown",
  "Home",
  "End",
];

export function PlaybackBar(props: {
  source: string;
  timestamp: number | undefined;
  paused: boolean;
  canToggle: boolean;
  canStep: boolean;
  onToggle: () => void;
  onStep: () => void;
  events: EventEntry[];
  frameCount: number | undefined;
  currentFrame: number;
  canSeek: boolean;
  busy: boolean;
  onSeek: (frame: number) => void;
  eventsOpen: boolean;
  onToggleEvents: () => void;
  processingFps: unknown;
}) {
  const [seek, setSeek] = useState<number | null>(null);
  const { frameCount, events } = props;
  const last = Math.max(1, (frameCount ?? 1) - 1);
  const commit = () => {
    if (seek !== null) props.onSeek(seek);
    setSeek(null);
  };
  return (
    <footer className="playback">
      <div className="playback-label">
        <span>{props.source === "Replay" ? "Replay" : "Session"}</span>
        <small className="mono">
          {props.timestamp !== undefined ? formatDuration(props.timestamp) : "—"}
        </small>
      </div>
      <div className="playback-controls">
        <button
          className="play-button"
          disabled={!props.canToggle}
          onClick={props.onToggle}
          aria-label={props.paused ? "Resume processing" : "Pause processing"}
        >
          <Icon name={props.paused ? "play" : "pause"} size={22} />
        </button>
        <button
          className="icon-button step-button"
          disabled={!props.canStep}
          onClick={props.onStep}
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
          max={last}
          value={Math.min(seek ?? props.currentFrame, last)}
          disabled={!props.canSeek || !frameCount || props.busy}
          onChange={(e) => setSeek(Number(e.target.value))}
          onPointerUp={commit}
          onKeyUp={(e) => {
            if (SEEK_KEYS.includes(e.key) && seek !== null) commit();
          }}
        />
        <div className="timeline-caption">
          <span>
            {props.canSeek
              ? "Seek a recorded frame"
              : "Frame seek is available for supported replay sources"}
          </span>
          <span className="mono">
            {frameCount
              ? `${props.currentFrame + 1} / ${frameCount} frames`
              : "Live progression"}
          </span>
        </div>
      </div>
      <button
        className={`event-button ${props.eventsOpen ? "active" : ""}`}
        onClick={props.onToggleEvents}
      >
        <span className="dot amber" />
        {events.length} events
        <Icon name="down" size={14} />
      </button>
      <div className="session-rate">
        <strong className="mono">{metric(props.processingFps)}</strong>
        <span>processing fps</span>
      </div>
    </footer>
  );
}
