import { useEffect, useRef, useState } from "react";
import type { EventEntry, Notice } from "../../hooks/useStudioSession";
import { Icon } from "../Icon";

export function EventsDrawer({
  events,
  canSeek,
  onSeek,
  onClose,
}: {
  events: EventEntry[];
  canSeek: boolean;
  onSeek: (frame: number) => void;
  onClose: () => void;
}) {
  return (
    <section className="events-drawer glass">
      <div className="section-title">
        Session events
        <button
          className="icon-button"
          onClick={onClose}
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
              onClick={() => onSeek(event.frame)}
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
  );
}

/** Non-error notices dismiss themselves after this long, unless hovered or focused. */
export const TOAST_MS = 5000;

export function Toast({
  notice,
  onDismiss,
}: {
  notice: NonNullable<Notice>;
  onDismiss: () => void;
}) {
  const [held, setHeld] = useState(false);
  const dismiss = useRef(onDismiss);
  dismiss.current = onDismiss;
  // Errors stay until dismissed; a new notice restarts the timer.
  useEffect(() => {
    if (notice.bad || held) return;
    const timer = setTimeout(() => dismiss.current(), TOAST_MS);
    return () => clearTimeout(timer);
  }, [notice, held]);
  return (
    <div
      className={`toast ${notice.bad ? "error" : ""}`}
      role={notice.bad ? "alert" : "status"}
      onMouseEnter={() => setHeld(true)}
      onMouseLeave={() => setHeld(false)}
      onFocus={() => setHeld(true)}
      onBlur={() => setHeld(false)}
    >
      <Icon name={notice.bad ? "alert" : "check"} size={18} />
      <span>{notice.text}</span>
      <button
        className="icon-button"
        aria-label="Dismiss message"
        onClick={onDismiss}
      >
        <Icon name="close" size={14} />
      </button>
    </div>
  );
}

export function LoginPanel({
  onLogin,
}: {
  /** Resolves true once the session cookie is set; failures are reported by the caller. */
  onLogin: (token: string) => Promise<boolean>;
}) {
  const [token, setToken] = useState(""),
    [busy, setBusy] = useState(false);
  const login = async () => {
    setBusy(true);
    try {
      if (await onLogin(token)) setToken("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="session-login glass">
      <h2>Connect to AetherTwin</h2>
      <p className="caption">
        Run the local service, or enter the access credential configured by its
        operator.
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
        disabled={busy || !token}
      >
        {busy ? "Connecting…" : "Open session"}
      </button>
      <span className="caption">
        Credentials stay in an HTTP-only session cookie.
      </span>
    </section>
  );
}

export function LiveHealthPanel({
  source,
  liveAvailable,
  onReturn,
}: {
  source: string;
  liveAvailable: boolean;
  onReturn: () => void;
}) {
  return (
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
          {liveAvailable ? "Configured · select explicitly" : "Not configured"}
        </dd>
      </dl>
      <p className="caption">
        This health view keeps the current acquisition source.{" "}
        {liveAvailable
          ? "Choose the live rig in the data source menu to request live acquisition."
          : "A measured twin and connected sensors are required before live acquisition can start."}
      </p>
      <button className="button ghost" onClick={onReturn}>
        Return to explorer <Icon name="arrow" size={15} />
      </button>
    </section>
  );
}
